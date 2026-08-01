"""Cross-transport parity: one spec, identical behaviour on and off HTTP.

The HTTP view path and the transport-neutral ``dispatch_spec`` path historically
ran two copies of the same sequence (validate → pool → service → output selector
→ status). Each shared leaf in the package — ``base_pool``,
``base_serializer_context``, ``build_input_serializer_from_data``,
``apply_queryset_shaping``, ``resolve_success_status``, ``map_service_error`` —
was extracted *after* the two copies were caught disagreeing.

This module is the standing assertion that they don't. Every test here declares
one spec, exercises it over both surfaces, and asserts the surfaces agree; a
behaviour that only one path implements is a defect regardless of which path
has it. Keep new spec fields covered here — a field honoured on one transport
and silently ignored on the other is the failure mode this file exists to catch.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceViewSet,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.viewsets.selector_viewset import SelectorViewSet
from tests.testapp.models import Post


class _TitleInput(serializers.Serializer):
    title = serializers.CharField()
    tenant = serializers.CharField()


def _echo(*, data: Any) -> dict[str, Any]:
    return dict(data)


def _post_serializer() -> type[serializers.ModelSerializer]:
    class _PostSerializer(serializers.ModelSerializer):
        class Meta:
            model = Post
            fields = ("id", "title")

    return _PostSerializer


def _create_view(spec: ServiceSpec[Any, Any, Any], **attrs: Any) -> Any:
    """A ``create``-wired viewset for ``spec``, plus any extra class attributes."""
    namespace: dict[str, Any] = {
        "queryset": Post.objects.all(),
        "action_specs": {"create": spec},
        **attrs,
    }
    viewset = type("_VS", (ServiceViewSet,), namespace)
    return viewset.as_view({"post": "create"})


# --- input_data ----------------------------------------------------------
#
# ``ServiceSpec.input_data`` merges server-provided keys on top of the client
# payload before the input serializer validates it — the seam that lifts route
# captures into fields a serializer can cross-validate. It was resolved only by
# the HTTP hook chain, so off HTTP the key simply never arrived and the
# serializer rejected the payload as incomplete.


_INPUT_DATA_SPEC = ServiceSpec(
    service=_echo,
    input_serializer=_TitleInput,
    input_data=lambda: {"tenant": "server-supplied"},
)


@pytest.mark.django_db
def test_input_data_applies_over_http() -> None:
    response = _create_view(_INPUT_DATA_SPEC)(
        APIRequestFactory().post("/x/", {"title": "t"}, format="json")
    )
    assert response.data == {"title": "t", "tenant": "server-supplied"}


@pytest.mark.django_db
def test_input_data_applies_off_http() -> None:
    result = dispatch_spec(_INPUT_DATA_SPEC, user=None, params={"title": "t"})
    assert result.value == {"title": "t", "tenant": "server-supplied"}


@pytest.mark.django_db
def test_input_data_client_value_loses_to_provider_off_http() -> None:
    """Server-provided keys win on overlap — the same rule as the HTTP merge."""
    result = dispatch_spec(
        _INPUT_DATA_SPEC, user=None, params={"title": "t", "tenant": "client-supplied"}
    )
    assert result.value["tenant"] == "server-supplied"


# --- async services ------------------------------------------------------
#
# ``run_selector`` bridges an async callable from sync code; ``run_service`` did
# not, so a sync ``dispatch_spec`` over an async service returned the coroutine
# object itself — no exception, and under ``atomic=True`` the transaction
# committed before the body would have run.


async def _async_echo(*, data: Any) -> dict[str, Any]:
    return dict(data)


_ASYNC_SPEC = ServiceSpec(service=_async_echo, input_serializer=_TitleInput)


@pytest.mark.django_db(transaction=True)
def test_async_service_resolves_over_http() -> None:
    response = _create_view(_ASYNC_SPEC)(
        APIRequestFactory().post("/x/", {"title": "t", "tenant": "a"}, format="json")
    )
    assert response.data == {"title": "t", "tenant": "a"}


@pytest.mark.django_db(transaction=True)
def test_async_service_resolves_off_http() -> None:
    result = dispatch_spec(_ASYNC_SPEC, user=None, params={"title": "t", "tenant": "a"})
    assert not inspect.iscoroutine(result.value), (
        "sync dispatch_spec returned an un-awaited coroutine for an async service"
    )
    assert result.value == {"title": "t", "tenant": "a"}


# --- sync/async dispatch parity ------------------------------------------
#
# ``adispatch_spec`` is the async twin of ``dispatch_spec``, and a fix applied to
# one is a defect in the other until it is applied to both. These pin the twin to
# the same spec-field behaviour rather than trusting that the pair stay in step.


@pytest.mark.django_db(transaction=True)
async def test_input_data_applies_under_async_dispatch() -> None:
    result = await adispatch_spec(_INPUT_DATA_SPEC, user=None, params={"title": "t"})
    assert result.value == {"title": "t", "tenant": "server-supplied"}


@pytest.mark.django_db(transaction=True)
async def test_input_data_applies_under_async_bulk_dispatch() -> None:
    """A ``many=True`` payload is a list, so the merge lands on every item."""
    spec = ServiceSpec(
        service=lambda *, data: [dict(item) for item in data],
        input_serializer=_TitleInput,
        many=True,
        input_data=lambda: {"tenant": "server-supplied"},
    )
    result = await adispatch_spec(spec, user=None, params=[{"title": "a"}, {"title": "b"}])
    assert result.value == [
        {"title": "a", "tenant": "server-supplied"},
        {"title": "b", "tenant": "server-supplied"},
    ]


@pytest.mark.django_db
def test_input_data_applies_to_bulk_off_http() -> None:
    spec = ServiceSpec(
        service=lambda *, data: [dict(item) for item in data],
        input_serializer=_TitleInput,
        many=True,
        input_data=lambda: {"tenant": "server-supplied"},
    )
    result = dispatch_spec(spec, user=None, params=[{"title": "a"}])
    assert result.value == [{"title": "a", "tenant": "server-supplied"}]


# --- view hook chains on the bulk path -----------------------------------
#
# The bulk path already routes through ``dispatch_spec``, and that partial
# convergence dropped the view's hook chains on the way: a spec that grew
# ``many=True`` silently stopped seeing ``get_service_kwargs`` /
# ``get_input_data`` / the serializer-context hooks that the single-instance
# path honours.


def _capture(seen: dict[str, Any]) -> Any:
    def service(*, data: Any, tenant: Any = None) -> list[Any]:
        seen["tenant"] = tenant
        seen["data"] = data
        return []

    return service


@pytest.mark.django_db
def test_service_kwargs_hook_applies_to_single() -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_capture(seen), input_serializer=_TitleInput)
    _create_view(spec, get_service_kwargs=lambda self: {"tenant": "from-view-hook"})(
        APIRequestFactory().post("/x/", {"title": "t", "tenant": "x"}, format="json")
    )
    assert seen["tenant"] == "from-view-hook"


@pytest.mark.django_db
def test_service_kwargs_hook_applies_to_bulk() -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_capture(seen), input_serializer=_TitleInput, many=True)
    _create_view(spec, get_service_kwargs=lambda self: {"tenant": "from-view-hook"})(
        APIRequestFactory().post("/x/", [{"title": "t", "tenant": "x"}], format="json")
    )
    assert seen["tenant"] == "from-view-hook"


@pytest.mark.django_db
def test_input_data_hook_applies_to_bulk() -> None:
    """``get_input_data`` feeds the serializer on the bulk path too."""
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_capture(seen), input_serializer=_TitleInput, many=True)
    _create_view(spec, get_input_data=lambda self, request: {"tenant": "from-view-hook"})(
        APIRequestFactory().post("/x/", [{"title": "t"}], format="json")
    )
    assert [dict(item) for item in seen["data"]] == [{"title": "t", "tenant": "from-view-hook"}]


@pytest.mark.django_db
def test_input_serializer_context_hook_applies_to_bulk() -> None:
    """A validator reading ``self.context`` sees the view's context on bulk too."""
    seen: dict[str, Any] = {}

    class _ContextReadingInput(serializers.Serializer):
        title = serializers.CharField()

        def validate(self, attrs: Any) -> Any:
            # A distinct key: the service below also writes to ``seen``, and it
            # runs *after* validation — same key would silently overwrite this.
            seen["context_tenant"] = self.context.get("tenant")
            return attrs

    spec = ServiceSpec(service=_capture(seen), input_serializer=_ContextReadingInput, many=True)
    _create_view(
        spec,
        get_input_serializer_context=lambda self: {"tenant": "from-view-hook"},
    )(APIRequestFactory().post("/x/", [{"title": "t"}], format="json"))
    assert seen["context_tenant"] == "from-view-hook"


# --- object permissions on a selector-resolved target --------------------
#
# ``enforce_permissions`` runs ``has_object_permission`` against the resolved row
# off HTTP. The HTTP retrieve-selector mixins replace DRF's ``get_object()`` —
# which runs ``check_object_permissions`` — with ``dispatch_selector_for_spec``,
# which did not, so the object-level half of a spec's permissions was enforced
# over MCP and skipped over HTTP.


class _DenyObject(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return False


@pytest.mark.django_db
def test_retrieve_selector_runs_object_permissions_over_http() -> None:
    post = Post.objects.create(title="secret")
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: Post.objects.filter(pk=pk),
        output_serializer=_post_serializer(),
        permission_classes=[_DenyObject],
    )
    viewset = type(
        "_VS",
        (SelectorViewSet,),
        {"queryset": Post.objects.all(), "action_specs": {"retrieve": spec}},
    )
    response = viewset.as_view({"get": "retrieve"})(
        APIRequestFactory().get(f"/x/{post.pk}/"), pk=post.pk
    )
    assert response.status_code == 403
