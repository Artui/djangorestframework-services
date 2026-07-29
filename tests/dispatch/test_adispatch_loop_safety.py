"""Every user-supplied sync callable ``adispatch_spec`` invokes runs off the loop.

A spec is written once and dispatched over both transports, so none of its
callables are ``async def``. Calling one from the event loop raises
``SynchronousOnlyOperation`` the moment it touches the ORM — a failure that
shows up only under the async transport, and only for the specs that happen to
query. Each test here queries inside the callable under test: on the event loop
it raises, in the thread-sensitive executor it doesn't.

The spec's own selector / service are covered by ``test_adispatch_spec.py`` —
they may legitimately be async, so they go through ``arun_callable`` instead.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db.models import QuerySet

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    adispatch_spec,
)
from tests.testapp.models import Post


def _all_posts() -> QuerySet[Post]:
    return Post.objects.all().order_by("id")


def _posts_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _count_posts(view: Any = None, request: Any = None) -> int:
    """A query — the thing that raises when it runs on the loop."""
    return Post.objects.count()


class _EvaluatingFilterSet:
    """A ``filter_set`` whose ``.qs`` needs rows to decide — it evaluates.

    Stands in for a django-filter ``FilterSet`` with a ``filter_<name>`` method
    that queries, or a ``ModelChoiceFilter`` whose ``is_valid()`` resolves the
    choice against the DB.
    """

    def __init__(self, *, data: Any, queryset: Any) -> None:
        self._queryset = queryset

    @property
    def qs(self) -> Any:
        allowed = [post.pk for post in Post.objects.all()]
        return self._queryset.filter(pk__in=allowed)


def _extend_by_query(qs: Any, view: Any, request: Any) -> Any:
    return qs.filter(pk__in=[post.pk for post in Post.objects.all()])


def _scope_provider(view: Any, request: Any) -> dict[str, Any]:
    """The documented headline use: a scoping lookup against another table."""
    return {"seen": _count_posts()}


def _service(*, data: Any = None, **kwargs: Any) -> Any:
    return Post.objects.first()


@pytest.mark.django_db(transaction=True)
class TestSelectorPathStaysOffTheLoop:
    async def test_filter_set_that_queries(self) -> None:
        await Post.objects.acreate(title="a")
        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=_all_posts, filter_set=_EvaluatingFilterSet
        )
        result = await adispatch_spec(spec, user=None, params={})
        assert await result.value.acount() == 1

    async def test_extend_queryset_that_queries(self) -> None:
        await Post.objects.acreate(title="a")
        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=_all_posts, extend_queryset=_extend_by_query
        )
        result = await adispatch_spec(spec, user=None, params={})
        assert await result.value.acount() == 1

    async def test_kwargs_provider_that_queries(self) -> None:
        await Post.objects.acreate(title="a")

        def selector(*, seen: int = -1) -> QuerySet[Post]:
            return Post.objects.filter(title="a") if seen == 1 else Post.objects.none()

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=selector, kwargs=_scope_provider)
        result = await adispatch_spec(spec, user=None, params={})
        assert await result.value.acount() == 1

    async def test_retrieve_shaping_that_queries(self) -> None:
        post = await Post.objects.acreate(title="a")
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_posts_by_pk, extend_queryset=_extend_by_query
        )
        result = await adispatch_spec(spec, user=None, params={"pk": post.pk})
        assert result.value.pk == post.pk


@pytest.mark.django_db(transaction=True)
class TestServicePathStaysOffTheLoop:
    async def test_instance_selector_shaping_and_provider_that_query(self) -> None:
        post = await Post.objects.acreate(title="a")
        spec = ServiceSpec(
            service=_service,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_posts_by_pk,
                kwargs=_scope_provider,
                extend_queryset=_extend_by_query,
            ),
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params={"pk": post.pk})
        assert result.value.pk == post.pk

    async def test_collection_selector_shaping_and_provider_that_query(self) -> None:
        await Post.objects.acreate(title="a")

        def bulk_service(*, collection: Any, **kwargs: Any) -> Any:
            return collection

        spec = ServiceSpec(
            service=bulk_service,
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_all_posts,
                kwargs=_scope_provider,
                extend_queryset=_extend_by_query,
            ),
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params={})
        assert await result.value.acount() == 1

    async def test_output_selector_shaping_that_queries(self) -> None:
        await Post.objects.acreate(title="a")
        spec = ServiceSpec(
            service=_service,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=_all_posts, extend_queryset=_extend_by_query
            ),
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params={})
        assert await result.value.acount() == 1

    async def test_kwargs_provider_that_queries(self) -> None:
        await Post.objects.acreate(title="a")

        def service(*, seen: int = -1, **kwargs: Any) -> int:
            return seen

        spec = ServiceSpec(service=service, kwargs=_scope_provider, atomic=False)
        result = await adispatch_spec(spec, user=None, params={})
        assert result.value == 1

    async def test_input_serializer_context_provider_that_queries(self) -> None:
        await Post.objects.acreate(title="a")
        spec = ServiceSpec(
            service=_service,
            input_serializer_context=_scope_provider,
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params={})
        assert result.value is not None

    async def test_callable_success_status_that_queries(self) -> None:
        await Post.objects.acreate(title="a")
        spec = ServiceSpec(
            service=_service,
            success_status=lambda *, result: 201 if _count_posts() else 200,
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params={})
        assert result.status == 201


@pytest.mark.django_db(transaction=True)
class TestBulkPathStaysOffTheLoop:
    async def test_provider_context_and_status_that_query(self) -> None:
        await Post.objects.acreate(title="a")

        def bulk_service(*, seen: int = -1, **kwargs: Any) -> list[int]:
            return [seen]

        spec = ServiceSpec(
            service=bulk_service,
            many=True,
            kwargs=_scope_provider,
            input_serializer_context=_scope_provider,
            success_status=lambda *, result: 201 if _count_posts() else 200,
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params=[])
        assert result.value == [1]
        assert result.status == 201
