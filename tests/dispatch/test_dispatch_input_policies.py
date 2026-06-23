"""``dispatch_spec`` input policies — argument binding, unknown args, target guard."""

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
    dispatch_spec,
    enforce_permissions,
)
from tests.testapp.models import Post


@dataclass
class _PostIn:
    title: str


class _TitleSerializer(serializers.Serializer):
    """Dict-validating serializer (``validated_data`` is a plain dict)."""

    title = serializers.CharField()


def _post_qs_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _all_posts() -> QuerySet[Post]:
    return Post.objects.all().order_by("id")


# ---------------------------------------------------------------- argument binding


@pytest.mark.django_db
class TestArgumentBindingSelector:
    def test_auto_selector_spreads_author_wins(self) -> None:
        seen: dict[str, Any] = {}

        def only_titled(*, wanted: str) -> QuerySet[Post]:
            seen["wanted"] = wanted
            return Post.objects.filter(title=wanted)

        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=only_titled, kwargs=lambda: {"wanted": "author"}
        )
        # Client tries to override the author-scoped kwarg; AUTO (author wins) ignores it.
        dispatch_spec(spec, user=None, params={"wanted": "client"})
        assert seen["wanted"] == "author"

    def test_spread_caller_wins_lets_client_override(self) -> None:
        seen: dict[str, Any] = {}

        def only_titled(*, wanted: str) -> QuerySet[Post]:
            seen["wanted"] = wanted
            return Post.objects.none()

        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=only_titled, kwargs=lambda: {"wanted": "default"}
        )
        dispatch_spec(
            spec,
            user=None,
            params={"wanted": "client"},
            argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
        )
        assert seen["wanted"] == "client"

    def test_bundle_on_selector_does_not_spread_params(self) -> None:
        seen: dict[str, Any] = {}

        def with_default(*, wanted: str = "untouched") -> QuerySet[Post]:
            seen["wanted"] = wanted
            return Post.objects.none()

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=with_default)
        dispatch_spec(
            spec,
            user=None,
            params={"wanted": "client"},
            argument_binding=ArgumentBinding.BUNDLE,
        )
        assert seen["wanted"] == "untouched"

    def test_spread_strips_reserved_seed(self) -> None:
        seen: dict[str, Any] = {}

        def grab_user(*, user: Any) -> QuerySet[Post]:
            seen["user"] = user
            return Post.objects.none()

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=grab_user)
        # A client param named ``user`` must not override the real user seed.
        dispatch_spec(spec, user="real", params={"user": "spoof"})
        assert seen["user"] == "real"


@pytest.mark.django_db
class TestArgumentBindingService:
    def test_auto_service_bundles_no_spread(self) -> None:
        seen: dict[str, Any] = {}

        def svc(*, data: dict[str, Any], title: str = "unset") -> Post:
            seen["title_kwarg"] = title
            return Post.objects.create(title=data["title"])

        spec = ServiceSpec(service=svc, input_serializer=_TitleSerializer, atomic=False)
        dispatch_spec(spec, user=None, params={"title": "x"})
        # Under BUNDLE, the field reaches the callable only as ``data`` — not spread.
        assert seen["title_kwarg"] == "unset"

    def test_spread_author_wins_service(self) -> None:
        seen: dict[str, Any] = {}

        def svc(*, title: str) -> Post:
            seen["title"] = title
            return Post.objects.create(title=title)

        spec = ServiceSpec(
            service=svc,
            input_serializer=_TitleSerializer,
            kwargs=lambda: {"title": "author"},
            atomic=False,
        )
        dispatch_spec(
            spec,
            user=None,
            params={"title": "client"},
            argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        )
        assert seen["title"] == "author"

    def test_spread_caller_wins_service(self) -> None:
        seen: dict[str, Any] = {}

        def svc(*, title: str) -> Post:
            seen["title"] = title
            return Post.objects.create(title=title)

        spec = ServiceSpec(
            service=svc,
            input_serializer=_TitleSerializer,
            kwargs=lambda: {"title": "default"},
            atomic=False,
        )
        dispatch_spec(
            spec,
            user=None,
            params={"title": "client"},
            argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
        )
        assert seen["title"] == "client"


# ---------------------------------------------------------------- unknown arguments


@pytest.mark.django_db
class TestUnknownArguments:
    def test_ignore_drops_undeclared(self) -> None:
        def svc(*, data: dict[str, Any]) -> Post:
            assert "bogus" not in data
            return Post.objects.create(title=data["title"])

        spec = ServiceSpec(service=svc, input_serializer=_TitleSerializer, atomic=False)
        result = dispatch_spec(spec, user=None, params={"title": "x", "bogus": 1})
        assert result.value.title == "x"

    def test_reject_raises_on_undeclared_service(self) -> None:
        def svc(*, data: dict[str, Any]) -> Post:
            return Post.objects.create(title=data["title"])

        spec = ServiceSpec(service=svc, input_serializer=_TitleSerializer, atomic=False)
        with pytest.raises(ValidationError, match="bogus"):
            dispatch_spec(
                spec,
                user=None,
                params={"title": "x", "bogus": 1},
                unknown_arguments=UnknownArguments.REJECT,
            )

    def test_reject_allows_instance_selector_keys(self) -> None:
        post = Post.objects.create(title="old")

        def svc(*, instance: Post, data: dict[str, Any]) -> Post:
            instance.title = data["title"]
            instance.save(update_fields=["title"])
            return instance

        spec = ServiceSpec(
            service=svc,
            input_serializer=_TitleSerializer,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk
            ),
            atomic=False,
        )
        # ``pk`` is consumed by the instance selector, ``title`` is declared —
        # neither is "unknown" under REJECT.
        result = dispatch_spec(
            spec,
            user=None,
            params={"pk": post.pk, "title": "fresh"},
            unknown_arguments=UnknownArguments.REJECT,
        )
        assert result.value.title == "fresh"

    def test_reject_is_open_for_filtered_selector(self) -> None:
        class _FilterSet:
            def __init__(self, *, data: Any, queryset: QuerySet[Post]) -> None:
                self._queryset = queryset

            @property
            def qs(self) -> QuerySet[Post]:
                return self._queryset

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts, filter_set=_FilterSet)
        # A duck-typed filter_set makes the declared set unknowable → no rejection.
        result = dispatch_spec(
            spec,
            user=None,
            params={"anything": "goes"},
            unknown_arguments=UnknownArguments.REJECT,
        )
        assert result.kind == "list"

    def test_reject_raises_on_undeclared_selector(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk)
        with pytest.raises(ValidationError, match="bogus"):
            dispatch_spec(
                spec,
                user=None,
                params={"pk": 1, "bogus": 2},
                unknown_arguments=UnknownArguments.REJECT,
            )

    def test_passthrough_reaches_callable_via_data(self) -> None:
        seen: dict[str, Any] = {}

        def svc(*, data: dict[str, Any]) -> Post:
            seen["data"] = dict(data)
            return Post.objects.create(title=data["title"])

        spec = ServiceSpec(service=svc, input_serializer=_TitleSerializer, atomic=False)
        dispatch_spec(
            spec,
            user=None,
            params={"title": "x", "note": "kept"},
            unknown_arguments=UnknownArguments.PASSTHROUGH,
        )
        assert seen["data"] == {"title": "x", "note": "kept"}

    def test_passthrough_with_no_serializer_seeds_data(self) -> None:
        seen: dict[str, Any] = {}

        def svc(*, data: dict[str, Any]) -> Post:
            seen["data"] = dict(data)
            return Post.objects.create(title=data["note"])

        spec = ServiceSpec(service=svc, atomic=False)
        dispatch_spec(
            spec,
            user=None,
            params={"note": "from-passthrough"},
            unknown_arguments=UnknownArguments.PASSTHROUGH,
        )
        assert seen["data"] == {"note": "from-passthrough"}

    def test_passthrough_dataclass_spreads_extras(self) -> None:
        seen: dict[str, Any] = {}

        def svc(*, data: _PostIn, note: str = "unset") -> Post:
            seen["data_title"] = data.title
            seen["note"] = note
            return Post.objects.create(title=data.title)

        spec = ServiceSpec(service=svc, input_serializer=_PostIn, atomic=False)
        dispatch_spec(
            spec,
            user=None,
            params={"title": "x", "note": "spread"},
            argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
            unknown_arguments=UnknownArguments.PASSTHROUGH,
        )
        # Dataclass ``data`` stays intact; the extra reaches the callable via the spread.
        assert seen == {"data_title": "x", "note": "spread"}


# ---------------------------------------------------------------- target guard


class _Allow(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return True


class _DenyRequest(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return False


class _DenyObject(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return False


@pytest.mark.django_db
class TestTargetGuard:
    def test_guard_receives_none_on_create(self) -> None:
        seen: dict[str, Any] = {}

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            seen["instance"] = instance

        spec = ServiceSpec(
            service=lambda *, data: Post.objects.create(title=data.title),
            input_serializer=_PostIn,
            atomic=False,
        )
        dispatch_spec(spec, user=None, params={"title": "x"}, on_target_resolved=guard)
        assert seen["instance"] is None

    def test_guard_receives_resolved_instance_on_update(self) -> None:
        post = Post.objects.create(title="old")
        seen: dict[str, Any] = {}

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            seen["instance"] = instance

        spec = ServiceSpec(
            service=lambda *, instance, data: instance,
            input_serializer=_PostIn,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk
            ),
            atomic=False,
        )
        dispatch_spec(
            spec, user=None, params={"pk": post.pk, "title": "x"}, on_target_resolved=guard
        )
        assert seen["instance"] == post

    def test_guard_receives_collection_on_bulk(self) -> None:
        Post.objects.create(title="a")
        seen: dict[str, Any] = {}

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            seen["is_qs"] = isinstance(instance, QuerySet)

        spec = ServiceSpec(
            service=lambda *, collection: {"n": collection.count()},
            collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts),
            atomic=False,
        )
        dispatch_spec(spec, user=None, params={}, on_target_resolved=guard)
        assert seen["is_qs"] is True

    def test_guard_receives_none_on_many(self) -> None:
        seen: dict[str, Any] = {}

        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            seen["instance"] = instance

        spec = ServiceSpec(
            service=lambda *, data: [Post.objects.create(title=i.title) for i in data],
            input_serializer=_PostIn,
            many=True,
            atomic=False,
        )
        dispatch_spec(spec, user=None, params=[{"title": "a"}], on_target_resolved=guard)
        assert seen["instance"] is None

    def test_guard_raise_aborts_before_service(self) -> None:
        def guard(spec: Any, context: Any, *, instance: Any = None) -> None:
            raise PermissionDenied("nope")

        def svc(*, data: _PostIn) -> Post:
            raise AssertionError("service must not run when the guard raises")

        spec = ServiceSpec(service=svc, input_serializer=_PostIn, atomic=False)
        with pytest.raises(PermissionDenied, match="nope"):
            dispatch_spec(spec, user=None, params={"title": "x"}, on_target_resolved=guard)
        assert Post.objects.count() == 0

    def test_enforce_permissions_denies_on_object(self) -> None:
        post = Post.objects.create(title="old")
        spec = ServiceSpec(
            service=lambda *, instance, data: instance,
            input_serializer=_PostIn,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk
            ),
            permission_classes=[_DenyObject],
            atomic=False,
        )
        with pytest.raises(PermissionDenied):
            dispatch_spec(
                spec,
                user=None,
                params={"pk": post.pk, "title": "x"},
                on_target_resolved=enforce_permissions,
            )

    def test_enforce_permissions_passes_when_allowed(self) -> None:
        post = Post.objects.create(title="old")
        spec = ServiceSpec(
            service=lambda *, instance, data: instance,
            input_serializer=_PostIn,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk
            ),
            permission_classes=[_Allow],
            atomic=False,
        )
        result = dispatch_spec(
            spec,
            user=None,
            params={"pk": post.pk, "title": "x"},
            on_target_resolved=enforce_permissions,
        )
        assert result.value == post

    def test_enforce_permissions_class_level_denies_on_create(self) -> None:
        # No instance (create) → only ``has_permission`` runs, and it denies.
        spec = ServiceSpec(
            service=lambda *, data: Post.objects.create(title=data.title),
            input_serializer=_PostIn,
            permission_classes=[_DenyRequest],
            atomic=False,
        )
        with pytest.raises(PermissionDenied):
            dispatch_spec(
                spec, user=None, params={"title": "x"}, on_target_resolved=enforce_permissions
            )
        assert Post.objects.count() == 0
