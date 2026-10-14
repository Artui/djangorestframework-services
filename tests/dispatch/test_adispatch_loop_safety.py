"""Every user-supplied sync callable ``adispatch_spec`` invokes runs off the loop.

A spec is written once and dispatched over both transports, so none of its
callables are ``async def``. Calling one from the event loop raises
``SynchronousOnlyOperation`` the moment it touches the ORM — a failure that
shows up only under the async transport, and only for the specs that happen to
query. Each test here queries inside the callable under test: on the event loop
it raises, in the thread-sensitive executor it doesn't.

The edge prepares the call in one executor hop and the kernel's ``adispatch``
runs the rest, so these hold the composition: whatever runs where, nothing a spec
declares reaches the loop. The spec's own selector / service are covered by
``test_adispatch_spec.py``, since they may legitimately be async.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated, Any

import django_filters
import pytest
from django.db.models import QuerySet
from rest_framework import serializers

from rest_framework_services import (
    DEFAULT_POOL_SEEDS,
    NotClientInput,
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


class _EvaluatingFilterSet(django_filters.FilterSet):
    """A ``filter_set`` whose filter method needs rows to decide: it evaluates."""

    seen = django_filters.NumberFilter(method="filter_seen")

    def filter_seen(self, queryset: Any, name: str, value: Any) -> Any:
        return queryset.filter(pk__in=[post.pk for post in Post.objects.all()])


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


def _extend_by_query(qs: Any, view: Any, request: Any) -> Any:
    return qs.filter(pk__in=[post.pk for post in Post.objects.all()])


def _scope_provider(view: Any, request: Any) -> dict[str, Any]:
    """The documented headline use: a scoping lookup against another table."""
    return {"seen": _count_posts()}


def _service(*, data: Any = None, **kwargs: Any) -> Any:
    return Post.objects.first()


def _recorder(into: list[Any], *, tag: Any = None) -> Any:
    def report(
        progress: float,
        *,
        total: float | None = None,
        message: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        into.append((tag, progress))

    return report


def _task_record_reporter(into: list[Any]) -> Callable[..., Any]:
    """A ``progress_reporter`` provider that queries while building its sink.

    The field's documented use is a task record, and finding or creating the
    record is a query made by the provider itself, before any report is sent.
    The row count it saw is stamped on every report, so a test can tell that the
    sink it built is the one the callable reported into.
    """

    def provider() -> Any:
        return _recorder(into, tag=_count_posts())

    return provider


@pytest.mark.django_db(transaction=True)
class TestSelectorPathStaysOffTheLoop:
    async def test_filter_set_that_queries(self) -> None:
        await Post.objects.acreate(title="a")
        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=_all_posts, filter_set=_EvaluatingFilterSet
        )
        result = await adispatch_spec(spec, user=None, params={"seen": 1})
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

    async def test_progress_reporter_provider_that_queries(self) -> None:
        await Post.objects.acreate(title="a")
        seen: list[Any] = []

        def selector(*, progress: Any) -> QuerySet[Post]:
            progress(1)
            return _all_posts()

        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=selector,
            progress_reporter=_task_record_reporter(seen),
        )
        result = await adispatch_spec(spec, user=None, params={})
        assert await result.value.acount() == 1
        assert seen == [(1, 1)]

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

    async def test_progress_reporter_provider_that_queries(self) -> None:
        await Post.objects.acreate(title="a")
        seen: list[Any] = []

        def service(*, progress: Any, **kwargs: Any) -> str:
            progress(1)
            return "done"

        spec = ServiceSpec(
            service=service, progress_reporter=_task_record_reporter(seen), atomic=False
        )
        # With a transport reporter beside it: the provider moving off the loop
        # must not change the fan-out, so both sinks still get the report.
        result = await adispatch_spec(
            spec, user=None, params={}, progress=_recorder(seen, tag="transport")
        )
        assert result.value == "done"
        assert seen == [("transport", 1), (1, 1)]

    async def test_input_serializer_context_provider_that_queries(self) -> None:
        await Post.objects.acreate(title="a")
        spec = ServiceSpec(
            service=_service,
            input_serializer_context=_scope_provider,
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params={})
        assert result.value is not None


@pytest.mark.django_db(transaction=True)
class TestBulkPathStaysOffTheLoop:
    async def test_provider_and_context_that_query(self) -> None:
        await Post.objects.acreate(title="a")

        def bulk_service(*, seen: int = -1, **kwargs: Any) -> list[int]:
            return [seen]

        spec = ServiceSpec(
            service=bulk_service,
            many=True,
            input_serializer=_TitleIn,
            kwargs=_scope_provider,
            input_serializer_context=_scope_provider,
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params=[{"title": "x"}])
        assert result.value == [1]

    async def test_progress_reporter_provider_that_queries(self) -> None:
        await Post.objects.acreate(title="a")
        seen: list[Any] = []

        def bulk_service(*, progress: Any, **kwargs: Any) -> list[str]:
            progress(1)
            return ["done"]

        spec = ServiceSpec(
            service=bulk_service,
            many=True,
            input_serializer=_TitleIn,
            progress_reporter=_task_record_reporter(seen),
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params=[{"title": "x"}])
        assert result.value == ["done"]
        assert seen == [(1, 1)]


def _guard_that_queries(seen: list[Any]) -> Callable[..., None]:
    """An ``on_target_resolved`` guard that reads the database, as a row check does."""

    def guard(spec: Any, context: Any, *, instance: Any) -> None:
        seen.append(_count_posts())

    return guard


@pytest.mark.django_db(transaction=True)
class TestTheEdgeStaysOffTheLoop:
    """What the edge itself runs: the target lookup and the guard, in its one hop,
    and a project's seed resolver, which the kernel resolves through the edge's
    wrapper."""

    async def test_a_service_specs_guard_that_queries(self) -> None:
        post = await Post.objects.acreate(title="a")
        seen: list[Any] = []
        spec = ServiceSpec(
            service=_service,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_posts_by_pk),
            atomic=False,
        )
        await adispatch_spec(
            spec, user=None, params={"pk": post.pk}, on_target_resolved=_guard_that_queries(seen)
        )
        assert seen == [1]

    async def test_a_selector_specs_guard_that_queries(self) -> None:
        await Post.objects.acreate(title="a")
        seen: list[Any] = []
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts)
        await adispatch_spec(
            spec, user=None, params={}, on_target_resolved=_guard_that_queries(seen)
        )
        assert seen == [1]

    async def test_a_pool_seed_that_queries(self) -> None:
        await Post.objects.acreate(title="a")

        def selector(*, seen: Annotated[int, NotClientInput]) -> QuerySet[Post]:
            return _all_posts() if seen == 1 else Post.objects.none()

        seeds = DEFAULT_POOL_SEEDS.extend(seen=lambda *, request: _count_posts())
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=selector)
        result = await adispatch_spec(spec, user=None, params={}, pool_seeds=seeds)
        assert await result.value.acount() == 1
