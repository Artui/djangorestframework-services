"""``adispatch_spec`` input policies — argument binding, unknown args, target guard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission

from rest_framework_services import (
    ArgumentBinding,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    UnknownArguments,
    adispatch_spec,
    enforce_permissions,
)
from tests.testapp.models import Post


@dataclass
class _PostIn:
    title: str


class _TitleSerializer(serializers.Serializer):
    title = serializers.CharField()


def _post_qs_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _all_posts() -> QuerySet[Post]:
    return Post.objects.all().order_by("id")


class _DenyObject(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return False


@pytest.mark.django_db(transaction=True)
class TestAArgumentBinding:
    async def test_selector_caller_wins(self) -> None:
        seen: dict[str, Any] = {}

        async def only_titled(*, wanted: str) -> QuerySet[Post]:
            seen["wanted"] = wanted
            return Post.objects.none()

        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=only_titled, kwargs=lambda: {"wanted": "default"}
        )
        await adispatch_spec(
            spec,
            user=None,
            params={"wanted": "client"},
            argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
        )
        assert seen["wanted"] == "client"

    async def test_service_spread_author_wins(self) -> None:
        seen: dict[str, Any] = {}

        async def svc(*, title: str) -> Post:
            seen["title"] = title
            return await Post.objects.acreate(title=title)

        spec = ServiceSpec(
            service=svc,
            input_serializer=_TitleSerializer,
            kwargs=lambda: {"title": "author"},
            atomic=False,
        )
        await adispatch_spec(
            spec,
            user=None,
            params={"title": "client"},
            argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        )
        assert seen["title"] == "author"


@pytest.mark.django_db(transaction=True)
class TestAUnknownArguments:
    async def test_reject_selector(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk)
        with pytest.raises(ValidationError, match="bogus"):
            await adispatch_spec(
                spec,
                user=None,
                params={"pk": 1, "bogus": 2},
                unknown_arguments=UnknownArguments.REJECT,
            )

    async def test_reject_service(self) -> None:
        async def svc(*, data: dict[str, Any]) -> Post:
            return await Post.objects.acreate(title=data["title"])

        spec = ServiceSpec(service=svc, input_serializer=_TitleSerializer, atomic=False)
        with pytest.raises(ValidationError, match="bogus"):
            await adispatch_spec(
                spec,
                user=None,
                params={"title": "x", "bogus": 1},
                unknown_arguments=UnknownArguments.REJECT,
            )

    async def test_passthrough_no_serializer_seeds_data(self) -> None:
        seen: dict[str, Any] = {}

        async def svc(*, data: dict[str, Any]) -> Post:
            seen["data"] = dict(data)
            return await Post.objects.acreate(title=data["note"])

        spec = ServiceSpec(service=svc, atomic=False)
        await adispatch_spec(
            spec,
            user=None,
            params={"note": "kept"},
            unknown_arguments=UnknownArguments.PASSTHROUGH,
        )
        assert seen["data"] == {"note": "kept"}


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
