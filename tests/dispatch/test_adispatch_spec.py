"""Async ``adispatch_spec`` — async + sync callables, DB-safe materialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import django_filters
import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    adispatch_spec,
)
from tests.testapp.models import Post


@dataclass
class _PostIn:
    title: str


def _post_qs_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


class _PublishedFilterSet(django_filters.FilterSet):
    """Narrows by ``?published=``; a duck-typed set is refused when the spec compiles."""

    published = django_filters.BooleanFilter()

    class Meta:
        model = Post
        fields = ("published",)


@pytest.mark.django_db(transaction=True)
class TestADispatchSelector:
    async def test_list_with_async_selector(self) -> None:
        await Post.objects.acreate(title="a")
        await Post.objects.acreate(title="b")

        async def all_posts() -> QuerySet[Post]:
            return Post.objects.all().order_by("id")

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=all_posts)
        result = await adispatch_spec(spec, user=None, params={})
        assert result.kind == "list"
        assert (
            await sync_to_async(lambda: list(result.value))()
            == await sync_to_async(lambda: list(Post.objects.order_by("id")))()
        )

    async def test_retrieve_with_sync_selector_runs_off_loop(self) -> None:
        post = await Post.objects.acreate(title="p")
        # A sync selector, which the kernel's adispatch runs in the executor.
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk)
        result = await adispatch_spec(spec, user=None, params={"pk": post.pk})
        assert result.kind == "instance"
        assert result.value.pk == post.pk

    async def test_retrieve_missing_not_found(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk)
        result = await adispatch_spec(spec, user=None, params={"pk": 9999})
        assert result == DispatchResult(kind="not_found")

    async def test_retrieve_allow_none(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk, allow_none=True)
        result = await adispatch_spec(spec, user=None, params={"pk": 9999})
        assert result == DispatchResult(kind="instance")

    async def test_object_does_not_exist_retrieve_not_found(self) -> None:
        async def strict_get(*, pk: int) -> Post:
            return await Post.objects.aget(pk=pk)

        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=strict_get)
        result = await adispatch_spec(spec, user=None, params={"pk": 9999})
        assert result.kind == "not_found"

    async def test_object_does_not_exist_list_reraises(self) -> None:
        async def strict_get(*, pk: int) -> Post:
            return await Post.objects.aget(pk=pk)

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=strict_get)
        with pytest.raises(Post.DoesNotExist):
            await adispatch_spec(spec, user=None, params={"pk": 9999})

    async def test_selector_required(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST)
        with pytest.raises(ImproperlyConfigured, match="declares no selector"):
            await adispatch_spec(spec, user=None, params={})


@pytest.mark.django_db(transaction=True)
class TestADispatchService:
    async def test_create_with_async_service(self) -> None:
        async def acreate(*, data: _PostIn) -> Post:
            return await Post.objects.acreate(title=data.title)

        spec = ServiceSpec(service=acreate, input_serializer=_PostIn, atomic=False)
        result = await adispatch_spec(spec, user=None, params={"title": "new"})
        assert result.value.title == "new"

    async def test_create_with_sync_service_runs_off_loop(self) -> None:
        def create(*, data: _PostIn) -> Post:
            return Post.objects.create(title=data.title)

        spec = ServiceSpec(service=create, input_serializer=_PostIn, atomic=False)
        result = await adispatch_spec(spec, user=None, params={"title": "s"})
        assert result.value.title == "s"

    async def test_update_resolves_instance_async(self) -> None:
        post = await Post.objects.acreate(title="old")

        async def aupdate(*, instance: Post, data: _PostIn) -> Post:
            instance.title = data.title
            await instance.asave(update_fields=["title"])
            return instance

        spec = ServiceSpec(
            service=aupdate,
            input_serializer=_PostIn,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk
            ),
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params={"pk": post.pk, "title": "fresh"})
        assert result.kind == "instance"
        await post.arefresh_from_db()
        assert post.title == "fresh"

    async def test_missing_instance_not_found(self) -> None:
        async def aupdate(*, instance: Post, data: _PostIn) -> Post:
            return instance

        spec = ServiceSpec(
            service=aupdate,
            input_serializer=_PostIn,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk
            ),
        )
        result = await adispatch_spec(spec, user=None, params={"pk": 9999, "title": "x"})
        assert result.kind == "not_found"

    async def test_instance_selector_object_does_not_exist_not_found(self) -> None:
        async def strict_get(*, pk: int) -> Post:
            return await Post.objects.aget(pk=pk)

        async def aupdate(*, instance: Post, data: _PostIn) -> Post:
            return instance

        spec = ServiceSpec(
            service=aupdate,
            input_serializer=_PostIn,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=strict_get),
        )
        result = await adispatch_spec(spec, user=None, params={"pk": 9999, "title": "x"})
        assert result.kind == "not_found"

    async def test_output_selector_refetch_async(self) -> None:
        async def acreate(*, data: _PostIn) -> Post:
            return await Post.objects.acreate(title=data.title)

        async def refetch(*, result: Post) -> QuerySet[Post]:
            return Post.objects.filter(pk=result.pk)

        spec = ServiceSpec(
            service=acreate,
            input_serializer=_PostIn,
            output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=refetch),
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params={"title": "p"})
        assert result.value.title == "p"

    async def test_service_no_input_serializer(self) -> None:
        async def touch(*, user: Any) -> Post:
            return await Post.objects.acreate(title="t")

        spec = ServiceSpec(service=touch, atomic=False)
        result = await adispatch_spec(spec, user="bob", params={})
        assert result.value.title == "t"


class TestADispatchTypeError:
    async def test_rejects_non_spec(self) -> None:
        with pytest.raises(TypeError, match="ServiceSpec or SelectorSpec"):
            not_a_spec: Any = "nope"
            await adispatch_spec(not_a_spec, user=None, params={})


@pytest.mark.django_db(transaction=True)
class TestAFilterDataOnTheReadPath:
    """The async sibling had no ``filter_data`` parameter at all until 0.36.

    Passing one was a ``TypeError``, and nothing caught it because the only
    caller that passed it was sync-only. The pair is kept in step here.
    """

    async def test_filter_data_is_the_filter_source_when_given(self) -> None:
        await Post.objects.acreate(title="shipped", published=True)
        await Post.objects.acreate(title="draft", published=False)

        def all_posts() -> QuerySet[Post]:
            return Post.objects.all().order_by("id")

        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=all_posts, filter_set=_PublishedFilterSet
        )
        result = await adispatch_spec(spec, user=None, params={}, filter_data={"published": True})

        titles = await sync_to_async(lambda: [p.title for p in result.value])()
        assert titles == ["shipped"]

    async def test_omitting_filter_data_keeps_params_as_the_filter_source(self) -> None:
        await Post.objects.acreate(title="shipped", published=True)
        await Post.objects.acreate(title="draft", published=False)

        def all_posts() -> QuerySet[Post]:
            return Post.objects.all().order_by("id")

        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=all_posts, filter_set=_PublishedFilterSet
        )
        result = await adispatch_spec(spec, user=None, params={"published": False})

        titles = await sync_to_async(lambda: [p.title for p in result.value])()
        assert titles == ["draft"]
