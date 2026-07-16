"""Bulk dispatch — ``many=True`` and ``collection_selector_spec`` targets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from rest_framework import serializers

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceError,
    ServiceSpec,
    adispatch_spec,
    dispatch_spec,
    render_spec_output,
)
from tests.testapp.models import Post


@dataclass
class _PostIn:
    title: str


class _PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ("id", "title", "published")


class _PublishedFilterSet:
    def __init__(self, *, data: Any, queryset: QuerySet[Post]) -> None:
        self._data = data
        self._queryset = queryset

    @property
    def qs(self) -> QuerySet[Post]:
        raw = self._data.get("published")
        if raw is None:
            return self._queryset
        return self._queryset.filter(published=str(raw).lower() in ("1", "true", "yes"))


def _all_posts() -> QuerySet[Post]:
    return Post.objects.all().order_by("id")


def _bulk_create(*, data: list[_PostIn]) -> list[Post]:
    return [Post.objects.create(title=item.title) for item in data]


def _bulk_delete(*, collection: QuerySet[Post]) -> dict[str, int]:
    n, _ = collection.delete()
    return {"deleted": n}


def _bulk_publish(*, collection: QuerySet[Post]) -> list[int]:
    # Capture pks *before* the update so the output selector can re-fetch the
    # affected rows (the collection's own filter no longer matches post-update).
    ids = list(collection.values_list("id", flat=True))
    Post.objects.filter(id__in=ids).update(published=True)
    return ids


def _posts_by_ids(*, result: list[int]) -> QuerySet[Post]:
    return Post.objects.filter(id__in=result).order_by("id")


@pytest.mark.django_db
class TestDispatchMany:
    def test_list_in_list_out(self) -> None:
        spec = ServiceSpec(
            service=_bulk_create,
            input_serializer=_PostIn,
            many=True,
            success_status=201,
            atomic=False,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=_PostSerializer
            ),
        )
        result = dispatch_spec(spec, user=None, params=[{"title": "a"}, {"title": "b"}])
        assert result.kind == "list"
        assert result.status == 201
        rendered = render_spec_output(spec, result.value, many=True)
        assert [row["title"] for row in rendered] == ["a", "b"]
        assert Post.objects.count() == 2

    def test_callable_success_status_on_many(self) -> None:
        def status(*, result: list[Post]) -> int:
            return 201 if result else 200

        spec = ServiceSpec(
            service=_bulk_create,
            input_serializer=_PostIn,
            many=True,
            success_status=status,
            atomic=False,
        )
        assert dispatch_spec(spec, user=None, params=[{"title": "a"}]).status == 201

    def test_atomic_rollback(self) -> None:
        def boom(*, data: list[_PostIn]) -> None:
            Post.objects.create(title=data[0].title)
            raise ServiceError("partway")

        spec = ServiceSpec(service=boom, input_serializer=_PostIn, many=True, atomic=True)
        with pytest.raises(ServiceError):
            dispatch_spec(spec, user=None, params=[{"title": "a"}])
        assert Post.objects.count() == 0

    def test_many_without_input_serializer(self) -> None:
        # Degenerate but valid: no validation, the service ignores the payload.
        def make_two(*, user: Any) -> list[Post]:
            return [Post.objects.create(title="x"), Post.objects.create(title="y")]

        spec = ServiceSpec(service=make_two, many=True, atomic=False)
        result = dispatch_spec(spec, user=None, params=[{}, {}])
        assert result.kind == "list"
        assert len(result.value) == 2


@pytest.mark.django_db
class TestDispatchCollection:
    def test_bulk_delete_over_filtered_set(self) -> None:
        Post.objects.create(title="shipped", published=True)
        Post.objects.create(title="draft", published=False)
        spec = ServiceSpec(
            service=_bulk_delete,
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=_all_posts, filter_set=_PublishedFilterSet
            ),
            atomic=False,
        )
        result = dispatch_spec(spec, user=None, params={"published": "true"})
        assert result.kind == "instance"
        assert result.value == {"deleted": 1}
        assert set(Post.objects.values_list("title", flat=True)) == {"draft"}

    def test_empty_set_is_idempotent_noop(self) -> None:
        spec = ServiceSpec(
            service=_bulk_delete,
            collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts),
            atomic=False,
        )
        result = dispatch_spec(spec, user=None, params={})
        assert result.value == {"deleted": 0}

    def test_collection_selector_requires_selector(self) -> None:
        spec = ServiceSpec(
            service=_bulk_delete,
            collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST),
        )
        with pytest.raises(ImproperlyConfigured, match="requires a `selector`"):
            dispatch_spec(spec, user=None, params={})

    def test_collection_update_renders_list_output(self) -> None:
        # A LIST-kind output_selector_spec re-fetches and returns the affected
        # set, so a bulk update can render its result as a list (kind="list").
        Post.objects.create(title="a", published=False)
        Post.objects.create(title="b", published=False)
        Post.objects.create(title="c", published=True)
        spec = ServiceSpec(
            service=_bulk_publish,
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=_all_posts, filter_set=_PublishedFilterSet
            ),
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=_posts_by_ids, output_serializer=_PostSerializer
            ),
            atomic=False,
        )
        result = dispatch_spec(spec, user=None, params={"published": "false"})
        assert result.kind == "list"
        rendered = render_spec_output(spec, result.value, many=(result.kind == "list"))
        assert [row["title"] for row in rendered] == ["a", "b"]
        assert all(row["published"] for row in rendered)


@pytest.mark.django_db(transaction=True)
class TestADispatchBulk:
    async def test_amany_list_in_list_out(self) -> None:
        async def abulk_create(*, data: list[_PostIn]) -> list[Post]:
            return [await Post.objects.acreate(title=item.title) for item in data]

        spec = ServiceSpec(service=abulk_create, input_serializer=_PostIn, many=True, atomic=False)
        result = await adispatch_spec(spec, user=None, params=[{"title": "a"}, {"title": "b"}])
        assert result.kind == "list"
        assert len(result.value) == 2

    async def test_acallable_success_status_on_many(self) -> None:
        async def abulk_create(*, data: list[_PostIn]) -> list[Post]:
            return [await Post.objects.acreate(title=item.title) for item in data]

        def status(*, result: list[Post]) -> int:
            return 201 if result else 200

        spec = ServiceSpec(
            service=abulk_create,
            input_serializer=_PostIn,
            many=True,
            success_status=status,
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params=[{"title": "a"}])
        assert result.status == 201

    async def test_acollection_bulk_delete(self) -> None:
        await Post.objects.acreate(title="a", published=True)
        await Post.objects.acreate(title="b", published=False)

        async def abulk_delete(*, collection: QuerySet[Post]) -> dict[str, int]:
            n, _ = await collection.adelete()
            return {"deleted": n}

        async def aall_posts() -> QuerySet[Post]:
            return Post.objects.all()

        spec = ServiceSpec(
            service=abulk_delete,
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=aall_posts, filter_set=_PublishedFilterSet
            ),
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params={"published": "true"})
        assert result.value == {"deleted": 1}

    async def test_acollection_requires_selector(self) -> None:
        spec = ServiceSpec(
            service=_bulk_delete,
            collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST),
        )
        with pytest.raises(ImproperlyConfigured, match="requires a `selector`"):
            await adispatch_spec(spec, user=None, params={})

    async def test_amany_without_input_serializer(self) -> None:
        async def make_one(*, user: Any) -> list[Post]:
            return [await Post.objects.acreate(title="x")]

        spec = ServiceSpec(service=make_one, many=True, atomic=False)
        result = await adispatch_spec(spec, user=None, params=[{}])
        assert len(result.value) == 1

    async def test_acollection_update_renders_list_output(self) -> None:
        await Post.objects.acreate(title="a", published=False)
        await Post.objects.acreate(title="b", published=False)

        async def abulk_publish(*, collection: QuerySet[Post]) -> list[int]:
            ids = [pk async for pk in collection.values_list("id", flat=True)]
            await Post.objects.filter(id__in=ids).aupdate(published=True)
            return ids

        async def aall_posts() -> QuerySet[Post]:
            return Post.objects.all()

        async def aposts_by_ids(*, result: list[int]) -> QuerySet[Post]:
            return Post.objects.filter(id__in=result).order_by("id")

        spec = ServiceSpec(
            service=abulk_publish,
            collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=aall_posts),
            output_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=aposts_by_ids),
            atomic=False,
        )
        result = await adispatch_spec(spec, user=None, params={})
        assert result.kind == "list"
        # LIST output is the lazy shaped queryset — materialize it off the spec.
        posts = [p async for p in result.value]
        assert sorted(p.title for p in posts) == ["a", "b"]
        assert all(p.published for p in posts)
