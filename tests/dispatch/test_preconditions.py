"""``preconditions`` — state/DB business rules, on both dispatch paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.db.models import QuerySet
from rest_framework import serializers

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceError,
    ServiceSpec,
    adispatch_spec,
    dispatch_spec,
)
from tests.testapp.models import Post


@dataclass
class _TitleIn:
    title: str


class _TitleSerializer(serializers.Serializer):
    title = serializers.CharField()


def _rename(instance: Post, data: Any) -> Post:
    instance.title = data["title"]
    instance.save()
    return instance


def _all_posts() -> QuerySet[Post]:
    return Post.objects.all().order_by("id")


def _post_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _by_pk_spec() -> SelectorSpec[Any, Any]:
    """The async path resolves its own target — no ``instance=`` kwarg there."""
    return SelectorSpec[Any, Any](kind=SelectorKind.RETRIEVE, selector=_post_by_pk)


class _Locked(ServiceError):
    """A precondition failure — the shape a consumer's 409 takes."""


# --------------------------------------------------------------------------
# The pool a precondition sees
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_precondition_sees_validated_payload_and_resolved_instance() -> None:
    """One field covers both categories: the payload *and* the target."""
    seen: dict[str, Any] = {}

    def record(instance: Post, data: Any, user: Any) -> None:
        seen["instance"] = instance
        seen["data"] = data
        seen["user"] = user

    post = Post.objects.create(title="before")
    spec = ServiceSpec[Any, Any, Any](
        service=_rename,
        input_serializer=_TitleSerializer,
        preconditions=[record],
    )
    dispatch_spec(spec, user=None, params={"title": "after"}, instance=post)

    assert seen["instance"].pk == post.pk
    # Post-validation: the precondition sees validated data, not raw params.
    assert seen["data"]["title"] == "after"
    assert seen["user"] is None


@pytest.mark.django_db
def test_precondition_raise_aborts_before_the_service_runs() -> None:
    """Raise-to-abort, and the service must not have run."""

    def refuse(instance: Post) -> None:
        raise _Locked("this row is locked")

    post = Post.objects.create(title="before")
    spec = ServiceSpec[Any, Any, Any](
        service=_rename,
        input_serializer=_TitleSerializer,
        preconditions=[refuse],
    )
    with pytest.raises(_Locked):
        dispatch_spec(spec, user=None, params={"title": "after"}, instance=post)

    post.refresh_from_db()
    assert post.title == "before"


@pytest.mark.django_db
def test_preconditions_run_in_declaration_order() -> None:
    calls: list[str] = []
    spec = ServiceSpec[Any, Any, Any](
        service=_rename,
        input_serializer=_TitleSerializer,
        preconditions=[lambda: calls.append("first"), lambda: calls.append("second")],
    )
    dispatch_spec(
        spec,
        user=None,
        params={"title": "after"},
        instance=Post.objects.create(title="before"),
    )
    assert calls == ["first", "second"]


@pytest.mark.django_db
def test_return_value_is_ignored_a_false_predicate_is_a_no_op() -> None:
    """The documented footgun: ``-> bool`` returning ``False`` does nothing."""
    spec = ServiceSpec[Any, Any, Any](
        service=_rename,
        input_serializer=_TitleSerializer,
        preconditions=[lambda: False],
    )
    post = Post.objects.create(title="before")
    dispatch_spec(spec, user=None, params={"title": "after"}, instance=post)

    post.refresh_from_db()
    assert post.title == "after"


# --------------------------------------------------------------------------
# Selectors — one position, seeded by cardinality
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_retrieve_selector_seeds_instance() -> None:
    seen: dict[str, Any] = {}
    Post.objects.create(title="only")
    spec = SelectorSpec[Any, Any](
        kind=SelectorKind.RETRIEVE,
        selector=lambda: Post.objects.all().order_by("id"),
        preconditions=[lambda instance: seen.update(instance=instance)],
    )
    dispatch_spec(spec, user=None, params={})
    assert seen["instance"].title == "only"


@pytest.mark.django_db
def test_list_selector_seeds_collection() -> None:
    seen: dict[str, Any] = {}
    Post.objects.create(title="a")
    Post.objects.create(title="b")
    spec = SelectorSpec[Any, Any](
        kind=SelectorKind.LIST,
        selector=_all_posts,
        preconditions=[lambda collection: seen.update(count=collection.count())],
    )
    dispatch_spec(spec, user=None, params={})
    assert seen["count"] == 2


@pytest.mark.django_db
def test_retrieve_that_resolves_nothing_never_reaches_preconditions() -> None:
    """No target, no state rule to check — the 404 short-circuits first."""
    calls: list[int] = []
    spec = SelectorSpec[Any, Any](
        kind=SelectorKind.RETRIEVE,
        selector=lambda: Post.objects.none(),
        allow_none=True,
        preconditions=[lambda: calls.append(1)],
    )
    dispatch_spec(spec, user=None, params={})
    assert calls == []


# --------------------------------------------------------------------------
# Bulk — once, no target
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_bulk_runs_preconditions_once_with_no_target() -> None:
    calls: list[Any] = []

    def bulk_create(data: Any) -> list[Post]:
        return [Post.objects.create(title=item["title"]) for item in data]

    spec = ServiceSpec[Any, Any, Any](
        service=bulk_create,
        input_serializer=_TitleSerializer,
        many=True,
        preconditions=[lambda data: calls.append(data)],
    )
    dispatch_spec(spec, user=None, params=[{"title": "a"}, {"title": "b"}])

    assert len(calls) == 1
    assert [item["title"] for item in calls[0]] == ["a", "b"]


# --------------------------------------------------------------------------
# Async parity — same spec, same behaviour
# --------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_async_mutation_runs_preconditions_off_the_loop() -> None:
    """A sync precondition that queries must not raise SynchronousOnlyOperation."""
    seen: dict[str, Any] = {}

    def check(instance: Post, data: Any) -> None:
        # A real DB read: this is what off-loop execution buys.
        seen["exists"] = Post.objects.filter(pk=instance.pk).exists()
        seen["title"] = data["title"]

    post = await Post.objects.acreate(title="before")
    spec = ServiceSpec[Any, Any, Any](
        service=_rename,
        input_serializer=_TitleSerializer,
        instance_selector_spec=_by_pk_spec(),
        preconditions=[check],
    )
    await adispatch_spec(spec, user=None, params={"pk": post.pk, "title": "after"})

    assert seen == {"exists": True, "title": "after"}


@pytest.mark.django_db(transaction=True)
async def test_async_precondition_raise_aborts_before_the_service() -> None:
    def refuse() -> None:
        raise _Locked("nope")

    post = await Post.objects.acreate(title="before")
    spec = ServiceSpec[Any, Any, Any](
        service=_rename,
        input_serializer=_TitleSerializer,
        instance_selector_spec=_by_pk_spec(),
        preconditions=[refuse],
    )
    with pytest.raises(_Locked):
        await adispatch_spec(spec, user=None, params={"pk": post.pk, "title": "after"})

    await post.arefresh_from_db()
    assert post.title == "before"


@pytest.mark.django_db(transaction=True)
async def test_async_selector_seeds_instance_and_collection() -> None:
    seen: dict[str, Any] = {}
    await Post.objects.acreate(title="only")

    retrieve = SelectorSpec[Any, Any](
        kind=SelectorKind.RETRIEVE,
        selector=lambda: Post.objects.all().order_by("id"),
        preconditions=[lambda instance: seen.update(instance=instance.title)],
    )
    await adispatch_spec(retrieve, user=None, params={})

    listing = SelectorSpec[Any, Any](
        kind=SelectorKind.LIST,
        selector=_all_posts,
        preconditions=[lambda collection: seen.update(count=collection.count())],
    )
    await adispatch_spec(listing, user=None, params={})

    assert seen == {"instance": "only", "count": 1}


@pytest.mark.django_db(transaction=True)
async def test_async_bulk_runs_preconditions_once() -> None:
    calls: list[Any] = []

    def bulk_create(data: Any) -> list[Post]:
        return [Post.objects.create(title=item["title"]) for item in data]

    spec = ServiceSpec[Any, Any, Any](
        service=bulk_create,
        input_serializer=_TitleSerializer,
        many=True,
        preconditions=[lambda data: calls.append(len(data))],
    )
    await adispatch_spec(spec, user=None, params=[{"title": "a"}])
    assert calls == [1]
