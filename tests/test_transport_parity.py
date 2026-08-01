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
from django.contrib.auth.models import User
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.test import APIRequestFactory, force_authenticate

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceViewSet,
    adispatch_spec,
    build_offline_context,
    dispatch_spec,
)
from rest_framework_services.viewsets.selector_viewset import SelectorViewSet
from tests.testapp.models import Post


class _TitleOnlyInput(serializers.Serializer):
    title = serializers.CharField()


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


@pytest.mark.django_db
def test_input_data_over_http_survives_a_form_encoded_body() -> None:
    """A form body is a ``QueryDict`` (``{key: [values]}`` internally).

    Plain ``dict()``-ing or dict-unpacking one exposes those value *lists*, so
    every scalar field becomes ``['x']`` and validation fails. The merge has to
    go through the QueryDict-aware path — this pins it, because the hazard is
    invisible to JSON-only tests and the single-instance path routes its body
    through the shared core.
    """
    response = _create_view(_INPUT_DATA_SPEC)(
        APIRequestFactory().post("/x/", {"title": "t"}, format="multipart")
    )
    assert response.status_code == 201
    assert response.data == {"title": "t", "tenant": "server-supplied"}


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
def test_output_serializer_context_hook_applies_to_bulk() -> None:
    """The *output* context hook reaches the bulk renderer too.

    The last of the four chains, and the one that survives longest unnoticed —
    it only shows up when a spec both renders through an output serializer and
    reads view-supplied context.
    """
    seen: dict[str, Any] = {}

    class _Out(serializers.ModelSerializer):
        tenant = serializers.SerializerMethodField()

        class Meta:
            model = Post
            fields = ("id", "tenant")

        def get_tenant(self, obj: Any) -> Any:
            seen["tenant"] = self.context.get("tenant")
            return seen["tenant"]

    spec = ServiceSpec(
        service=lambda *, data: [Post.objects.create(title=item["title"]) for item in data],
        input_serializer=_TitleOnlyInput,
        many=True,
        output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Out),
    )
    _create_view(spec, get_output_serializer_context=lambda self: {"tenant": "from-view-hook"})(
        APIRequestFactory().post("/x/", [{"title": "a"}], format="json")
    )
    assert seen["tenant"] == "from-view-hook"


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


def _retrieve_viewset(spec: SelectorSpec[Any, Any]) -> Any:
    return type(
        "_VS",
        (SelectorViewSet,),
        {"queryset": Post.objects.all(), "action_specs": {"retrieve": spec}},
    ).as_view({"get": "retrieve"})


@pytest.mark.django_db
def test_retrieve_materialization_agrees_across_transports() -> None:
    """A RETRIEVE selector returning a queryset collapses the same way on both.

    ⚠ The selector *pool* deliberately differs — see the module note in
    ``selectors.utils`` — but what ``kind=RETRIEVE`` does to the selector's
    return must not, or the field would mean two things.
    """
    post = Post.objects.create(title="only")
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: Post.objects.filter(pk=pk),
        output_serializer=_post_serializer(),
    )
    http = _retrieve_viewset(spec)(APIRequestFactory().get(f"/x/{post.pk}/"), pk=post.pk)
    offline = dispatch_spec(spec, user=None, params={"pk": post.pk})
    assert http.data["id"] == post.pk
    assert offline.value.pk == post.pk


@pytest.mark.django_db
def test_retrieve_allow_none_agrees_across_transports() -> None:
    """A nullable retrieve that resolves nothing: 200 + null, and a null value."""
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: Post.objects.filter(pk=pk),
        output_serializer=_post_serializer(),
        allow_none=True,
    )
    http = _retrieve_viewset(spec)(APIRequestFactory().get("/x/999/"), pk=999)
    offline = dispatch_spec(spec, user=None, params={"pk": 999})
    assert http.status_code == 200
    assert http.data is None
    assert offline.kind == "instance"
    assert offline.value is None


@pytest.mark.django_db
def test_retrieve_missing_is_404_on_both() -> None:
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: Post.objects.filter(pk=pk),
        output_serializer=_post_serializer(),
    )
    http = _retrieve_viewset(spec)(APIRequestFactory().get("/x/999/"), pk=999)
    offline = dispatch_spec(spec, user=None, params={"pk": 999})
    assert http.status_code == 404
    assert (offline.kind, offline.status) == ("not_found", 404)


@pytest.mark.django_db
def test_selector_kwargs_hook_applies_over_http() -> None:
    """The selector chain survives the move onto the shared core."""
    seen: dict[str, Any] = {}

    def selector(*, pk: Any, tenant: Any = None) -> Any:
        seen["tenant"] = tenant
        return Post.objects.filter(pk=pk)

    post = Post.objects.create(title="p")
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=selector, output_serializer=_post_serializer()
    )
    viewset = type(
        "_VS",
        (SelectorViewSet,),
        {
            "queryset": Post.objects.all(),
            "action_specs": {"retrieve": spec},
            "get_selector_kwargs": lambda self: {"tenant": "from-view-hook"},
        },
    )
    viewset.as_view({"get": "retrieve"})(APIRequestFactory().get("/x/"), pk=post.pk)
    assert seen["tenant"] == "from-view-hook"


@pytest.mark.django_db
def test_query_params_do_not_become_selector_kwargs_over_http() -> None:
    """⚠ The one selector difference that is *intentional*, pinned.

    Off HTTP the flat ``params`` mapping is the argument channel and a selector
    spreads it. Over HTTP it is not — the query string belongs to ``filter_set``
    and the filter backends, and a selector's kwargs come from route captures
    plus the hook chain. Routing HTTP through the shared core must not quietly
    widen that channel, which is what ``argument_binding=BUNDLE`` prevents.
    """
    seen: dict[str, Any] = {}

    def selector(*, pk: Any, tenant: Any = "unset") -> Any:
        seen["tenant"] = tenant
        return Post.objects.filter(pk=pk)

    post = Post.objects.create(title="p")
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=selector, output_serializer=_post_serializer()
    )
    _retrieve_viewset(spec)(APIRequestFactory().get("/x/?tenant=client-supplied"), pk=post.pk)
    assert seen["tenant"] == "unset"

    # ...whereas off HTTP the same key *is* an argument, by design.
    offline = dispatch_spec(spec, user=None, params={"pk": post.pk, "tenant": "caller-supplied"})
    assert offline.value.pk == post.pk
    assert seen["tenant"] == "caller-supplied"


# --- reserved pool seeds are not route-capturable -------------------------
#
# ``RESERVED_POOL_SEEDS`` exists because a client-routable value named after a
# dispatcher seed would override the dispatcher's authoritative one — the
# constant's own docstring calls it a credential-spoofing footgun. The
# transport-neutral path strips them from URL kwargs; the HTTP selector path
# spread ``view.kwargs`` over ``base_pool`` and did not, so on a nested route
# like ``/users/<user>/posts/`` a capture named ``user`` shadowed the
# authenticated one — the selector scoped by the URL value, not the caller.


def _scoping_selector(seen: dict[str, Any]) -> Any:
    def selector(*, user: Any, pk: Any) -> Any:
        seen["user"] = user
        return Post.objects.filter(pk=pk)

    return selector


@pytest.mark.django_db
def test_url_kwarg_cannot_shadow_the_authenticated_user_over_http() -> None:
    seen: dict[str, Any] = {}
    real = User.objects.create(username="real")
    post = Post.objects.create(title="p")
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_scoping_selector(seen),
        output_serializer=_post_serializer(),
    )
    request = APIRequestFactory().get("/x/")
    force_authenticate(request, user=real)
    _retrieve_viewset(spec)(request, pk=post.pk, user="route-supplied")
    assert seen["user"] == real


@pytest.mark.django_db
def test_url_kwarg_cannot_shadow_the_authenticated_user_off_http() -> None:
    seen: dict[str, Any] = {}
    real = User.objects.create(username="real")
    post = Post.objects.create(title="p")
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_scoping_selector(seen),
        output_serializer=_post_serializer(),
    )
    context = build_offline_context(user=real, kwargs={"user": "route-supplied"})
    dispatch_spec(
        spec,
        user=real,
        params={"pk": post.pk},
        request=context.request,
        view=context.view,
    )
    assert seen["user"] == real


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
