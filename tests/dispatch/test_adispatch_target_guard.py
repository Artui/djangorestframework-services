"""``adispatch_spec``'s target guard: what ``on_target_resolved`` is asked about.

The argument policies are the kernel's ``adispatch``'s and are tested there; the
guard is the edge's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.db.models import QuerySet
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    adispatch_spec,
    enforce_permissions,
)
from tests.testapp.models import Post


@dataclass
class _PostIn:
    title: str


def _post_qs_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _all_posts() -> QuerySet[Post]:
    return Post.objects.all().order_by("id")


class _DenyObject(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return False


class _DenyRequest(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return False


@pytest.mark.django_db(transaction=True)
class TestATargetGuard:
    async def test_guard_receives_instance_on_update(self) -> None:
        post = await Post.objects.acreate(title="old")
        seen: dict[str, Any] = {}

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            seen["instance_pk"] = instance.pk

        async def svc(*, instance: Post, data: _PostIn) -> Post:
            return instance

        spec = ServiceSpec(
            service=svc,
            input_serializer=_PostIn,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk
            ),
            atomic=False,
        )
        await adispatch_spec(
            spec, user=None, params={"pk": post.pk, "title": "x"}, on_target_resolved=guard
        )
        assert seen["instance_pk"] == post.pk

    async def test_guard_receives_none_on_many(self) -> None:
        seen: dict[str, Any] = {}

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            seen["instance"] = instance

        async def bulk(*, data: list[_PostIn]) -> list[Post]:
            return [await Post.objects.acreate(title=i.title) for i in data]

        spec = ServiceSpec(service=bulk, input_serializer=_PostIn, many=True, atomic=False)
        await adispatch_spec(spec, user=None, params=[{"title": "a"}], on_target_resolved=guard)
        assert seen["instance"] is None

    async def test_enforce_permissions_denies_on_object(self) -> None:
        post = await Post.objects.acreate(title="old")

        async def svc(*, instance: Post, data: _PostIn) -> Post:
            return instance

        spec = ServiceSpec(
            service=svc,
            input_serializer=_PostIn,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk
            ),
            permission_classes=[_DenyObject],
            atomic=False,
        )
        with pytest.raises(PermissionDenied):
            await adispatch_spec(
                spec,
                user=None,
                params={"pk": post.pk, "title": "x"},
                on_target_resolved=enforce_permissions,
            )

    # -- selector dispatch fires the guard --

    async def test_guard_receives_instance_on_retrieve_selector(self) -> None:
        post = await Post.objects.acreate(title="a")
        seen: dict[str, Any] = {}

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            seen["instance_pk"] = instance.pk

        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk)
        await adispatch_spec(spec, user=None, params={"pk": post.pk}, on_target_resolved=guard)
        assert seen["instance_pk"] == post.pk

    async def test_guard_receives_collection_on_list_selector(self) -> None:
        await Post.objects.acreate(title="a")
        seen: dict[str, Any] = {}

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            seen["is_qs"] = isinstance(instance, QuerySet)

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts)
        await adispatch_spec(spec, user=None, params={}, on_target_resolved=guard)
        assert seen["is_qs"] is True

    async def test_enforce_permissions_denies_object_on_retrieve_selector(self) -> None:
        post = await Post.objects.acreate(title="a")
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_post_qs_by_pk,
            permission_classes=[_DenyObject],
        )
        with pytest.raises(PermissionDenied):
            await adispatch_spec(
                spec, user=None, params={"pk": post.pk}, on_target_resolved=enforce_permissions
            )

    async def test_enforce_permissions_class_level_denies_on_list_selector(self) -> None:
        await Post.objects.acreate(title="a")
        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=_all_posts, permission_classes=[_DenyRequest]
        )
        with pytest.raises(PermissionDenied):
            await adispatch_spec(spec, user=None, params={}, on_target_resolved=enforce_permissions)

    async def test_enforce_permissions_allows_list_selector_despite_deny_object(self) -> None:
        # Collection-safe: the LIST queryset skips has_object_permission.
        await Post.objects.acreate(title="a")
        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=_all_posts, permission_classes=[_DenyObject]
        )
        result = await adispatch_spec(
            spec, user=None, params={}, on_target_resolved=enforce_permissions
        )
        assert result.kind == "list"
