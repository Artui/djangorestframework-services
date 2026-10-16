"""A ``NotClientInput`` parameter never receives the client's validated value.

The kernel hands a service each validated value by name, beside ``data``. A
name any callable of the call marks ``NotClientInput`` is the server's for the
whole call, so an input serializer's field of that name is the client's only
inside ``data``: the parameter gets the server's value - a view hook's, the
spec's ``kwargs`` provider's, ``input_data``'s - or its default, over HTTP and
off it, on both cores, single and bulk. Each test is written against the
public signatures and asserts what 0.57.0 answered.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Annotated, Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.dispatch.grant import Grant
from django_service_specs.specs.kernel_spec import kernel_spec
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DispatchResult,
    NotClientInput,
    SelectorListView,
    SelectorSpec,
    ServiceCreateView,
    ServiceSpec,
    ServiceUpdateView,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.offline_service_view import OfflineServiceView
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.view_hooks import ViewHooks
from tests.testapp.models import Post

_factory = APIRequestFactory()

Hidden = Annotated[str, NotClientInput]
SEEN: dict[str, Any] = {}


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch_with", [_sync, _async], ids=["sync", "async"])
_BINDINGS = pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.AUTO, ArgumentBinding.BUNDLE, ArgumentBinding.SPREAD_AUTHOR_WINS],
    ids=["auto", "bundle", "spread"],
)


@pytest.fixture(autouse=True)
def _clear() -> None:
    SEEN.clear()


class _TenantIn(serializers.Serializer):
    tenant = serializers.CharField()
    title = serializers.CharField(required=False)


class _SourcedIn(serializers.Serializer):
    tenant_name = serializers.CharField(source="tenant")


class _IdIn(serializers.Serializer):
    tenant = serializers.IntegerField()


class _NestedTenant(serializers.Serializer):
    tenant = serializers.CharField()


class _FlattenedIn(serializers.Serializer):
    title = serializers.CharField()
    nested = _NestedTenant(source="*")


class _AddsTenantIn(serializers.Serializer):
    title = serializers.CharField()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return {**attrs, "tenant": "validate-added"}


@dataclasses.dataclass
class _TenantDC:
    tenant: str


def _hides(*, data: Any, tenant: Hidden = "server-default") -> None:
    """A service marking ``tenant`` as the server's."""
    SEEN["tenant"] = tenant
    SEEN["data"] = _plain_data(data)


def _plain_data(data: Any) -> Any:
    if isinstance(data, list):
        return [dict(row) for row in data]
    if dataclasses.is_dataclass(data) and not isinstance(data, type):
        return dataclasses.asdict(data)
    return dict(data)


def _plain(*, data: Any, tenant: str = "server-default") -> None:
    """A service taking ``tenant`` plainly: another callable of the call hides it."""
    SEEN["tenant"] = tenant
    SEEN["data"] = dict(data)


def _spec(serializer: Any = _TenantIn, **kwargs: Any) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(service=_hides, input_serializer=serializer, atomic=False, **kwargs)


# --- the client's value stays in ``data`` -----------------------------------


@_CORES
@_BINDINGS
def test_a_client_value_for_a_hidden_parameter_stays_in_data(
    dispatch_with: Dispatch, binding: ArgumentBinding
) -> None:
    dispatch_with(
        _spec(), user=None, params={"tenant": "client", "title": "t"}, argument_binding=binding
    )

    assert SEEN == {"tenant": "server-default", "data": {"tenant": "client", "title": "t"}}


@_CORES
def test_reject_admits_a_field_named_like_a_hidden_parameter(dispatch_with: Dispatch) -> None:
    dispatch_with(
        _spec(),
        user=None,
        params={"tenant": "client"},
        unknown_arguments=UnknownArguments.REJECT,
    )

    assert SEEN == {"tenant": "server-default", "data": {"tenant": "client"}}


@_CORES
def test_reject_refuses_a_hidden_key_no_field_declares(dispatch_with: Dispatch) -> None:
    with pytest.raises(ValidationError) as excinfo:
        dispatch_with(
            _spec(_AddsTenantIn),
            user=None,
            params={"title": "t", "tenant": "client"},
            unknown_arguments=UnknownArguments.REJECT,
        )

    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'tenant'."]}
    assert SEEN == {}


class _HidesCreateView(ServiceCreateView):
    permission_classes = []
    spec = _spec()


@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_a_client_value_for_a_hidden_parameter_stays_in_data_over_http(fmt: str) -> None:
    response = _HidesCreateView.as_view()(
        _factory.post("/", {"tenant": "client", "title": "t"}, format=fmt)
    )

    assert response.status_code == 204, response.data
    assert SEEN == {"tenant": "server-default", "data": {"tenant": "client", "title": "t"}}


@_CORES
def test_a_kernel_dispatch_of_the_spec_keeps_the_client_value_in_data(
    dispatch_with: Dispatch,
) -> None:
    # The kernel's own entry point, reading the drfs spec through ``kernel_spec``:
    # the Validator is the spec's, not the edge's. ``dispatch_with`` only marks
    # that it is one rule, whichever core a transport takes.
    spec = _spec(permission_classes=[])
    compiled = kernel_spec(spec)
    principal = AnonymousUser()

    dispatch(
        compiled,
        principal=principal,
        arguments={"tenant": "client"},
        grant=Grant(compiled, principal),
    )

    assert SEEN == {"tenant": "server-default", "data": {"tenant": "client"}}


@_CORES
@pytest.mark.parametrize(
    ("serializer", "params", "data"),
    [
        (_SourcedIn, {"tenant_name": "client"}, {"tenant": "client"}),
        (_TenantDC, {"tenant": "client"}, {"tenant": "client"}),
        (
            _FlattenedIn,
            {"title": "t", "nested": {"tenant": "client"}},
            {"title": "t", "tenant": "client"},
        ),
    ],
    ids=["a-sourced-field", "a-dataclass", "a-flattened-nested-field"],
)
def test_a_validated_key_named_like_a_hidden_parameter_stays_in_data(
    dispatch_with: Dispatch, serializer: Any, params: dict[str, Any], data: dict[str, Any]
) -> None:
    dispatch_with(_spec(serializer), user=None, params=params)

    assert SEEN["tenant"] == "server-default"
    assert SEEN["data"] == data


@_CORES
def test_a_key_a_validate_adds_named_like_a_hidden_parameter_stays_in_data(
    dispatch_with: Dispatch,
) -> None:
    dispatch_with(_spec(_AddsTenantIn), user=None, params={"title": "t"})

    assert SEEN == {"tenant": "server-default", "data": {"title": "t", "tenant": "validate-added"}}


def _lookup(*, pk: int, tenant: Hidden = "lookup-default") -> Any:
    SEEN["lookup"] = tenant
    return Post.objects.filter(pk=pk)


def _check(*, tenant: Hidden = "check-default") -> None:
    SEEN["check"] = tenant


@pytest.mark.django_db
@_CORES
def test_a_key_the_target_lookup_hides_never_reaches_the_service_from_a_field(
    dispatch_with: Dispatch,
) -> None:
    post = Post.objects.create(title="p")
    spec = ServiceSpec(
        service=_plain,
        input_serializer=_TenantIn,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
        atomic=False,
    )

    dispatch_with(spec, user=None, params={"pk": post.pk, "tenant": "client"})

    assert SEEN == {
        "lookup": "lookup-default",
        "tenant": "server-default",
        "data": {"tenant": "client"},
    }


class _LookupHidesUpdateView(ServiceUpdateView):
    permission_classes = []
    spec = ServiceSpec(
        service=_plain,
        input_serializer=_TenantIn,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
        atomic=False,
    )


@pytest.mark.django_db
def test_a_key_the_target_lookup_hides_never_reaches_the_service_over_http() -> None:
    post = Post.objects.create(title="p")

    response = _LookupHidesUpdateView.as_view()(
        _factory.put("/", {"tenant": "client"}, format="json"), pk=post.pk
    )

    assert response.status_code == 204, response.data
    assert SEEN == {
        "lookup": "lookup-default",
        "tenant": "server-default",
        "data": {"tenant": "client"},
    }


def _touch(*, instance: Any, tenant: str = "server-default") -> None:
    SEEN["tenant"] = tenant


@pytest.mark.django_db
@_CORES
def test_a_route_capture_a_lookup_hides_reaches_a_service_parameter_of_its_name(
    dispatch_with: Dispatch,
) -> None:
    # The capture is seeded for the call, since the kernel hands a target lookup
    # no call extras, and a seed reaches every callable naming it. 0.57.0 handed
    # the service its default here.
    post = Post.objects.create(title="p")
    spec = ServiceSpec(
        service=_touch,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
        atomic=False,
    )

    dispatch_with(
        spec,
        user=None,
        params={},
        view=OfflineServiceView(request=None, kwargs={"pk": post.pk, "tenant": "route"}),
    )

    assert SEEN == {"lookup": "route", "tenant": "route"}


@_CORES
def test_a_key_a_precondition_hides_never_reaches_it_or_the_service_from_a_field(
    dispatch_with: Dispatch,
) -> None:
    spec = ServiceSpec(
        service=_plain, input_serializer=_TenantIn, preconditions=[_check], atomic=False
    )

    dispatch_with(spec, user=None, params={"tenant": "client"})

    assert SEEN == {
        "check": "check-default",
        "tenant": "server-default",
        "data": {"tenant": "client"},
    }


@pytest.mark.parametrize("over_http", [False, True], ids=["off-http", "over-http"])
def test_a_bulk_call_hands_a_hidden_parameter_no_row_value(over_http: bool) -> None:
    spec = _spec(many=True)
    rows = [{"tenant": "client"}]
    if over_http:

        class _View(ServiceCreateView):
            permission_classes = []

        _View.spec = spec
        response = _View.as_view()(_factory.post("/", rows, format="json"))
        assert response.status_code == 201, response.data
    else:
        _sync(spec, user=None, params=rows)

    assert SEEN == {"tenant": "server-default", "data": [{"tenant": "client"}]}


def _lists_for(*, tenant: Hidden = "selector-default") -> Any:
    SEEN["tenant"] = tenant
    return Post.objects.none()


class _PostOut(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ["id"]


_SELECTOR = SelectorSpec(kind=SelectorKind.LIST, selector=_lists_for, output_serializer=_PostOut)


@pytest.mark.django_db
@_CORES
def test_a_selectors_hidden_parameter_takes_no_client_value(dispatch_with: Dispatch) -> None:
    dispatch_with(_SELECTOR, user=None, params={"tenant": "client"})

    assert SEEN == {"tenant": "selector-default"}


class _SelectorView(SelectorListView):
    permission_classes = []
    spec = _SELECTOR


@pytest.mark.django_db
def test_a_selectors_hidden_parameter_takes_no_query_value_over_http() -> None:
    response = _SelectorView.as_view()(_factory.get("/?tenant=client"))

    assert response.status_code == 200, response.data
    assert SEEN == {"tenant": "selector-default"}


# --- the server's value reaches it ------------------------------------------


@_CORES
@_BINDINGS
def test_a_hooks_value_for_a_hidden_field_reaches_the_parameter(
    dispatch_with: Dispatch, binding: ArgumentBinding
) -> None:
    dispatch_with(
        _spec(),
        user=None,
        params={"tenant": "client"},
        argument_binding=binding,
        view_hooks=ViewHooks(extra_kwargs={"tenant": "hook"}),
    )

    # 0.57.0 handed the hook's value to the parameter beside the client's in
    # ``data``; the hook's value is now the field's as well.
    assert SEEN == {"tenant": "hook", "data": {"tenant": "hook"}}


@_CORES
def test_the_specs_provider_value_for_a_hidden_field_reaches_the_parameter(
    dispatch_with: Dispatch,
) -> None:
    dispatch_with(
        _spec(kwargs=lambda: {"tenant": "provider"}), user=None, params={"tenant": "client"}
    )

    assert SEEN == {"tenant": "provider", "data": {"tenant": "provider"}}


@_CORES
def test_a_hooks_value_naming_a_hidden_fields_source_reaches_the_parameter(
    dispatch_with: Dispatch,
) -> None:
    dispatch_with(
        _spec(_SourcedIn),
        user=None,
        params={"tenant_name": "client"},
        view_hooks=ViewHooks(extra_kwargs={"tenant": "hook"}),
    )

    assert SEEN == {"tenant": "hook", "data": {"tenant": "hook"}}


@_CORES
def test_a_hooks_value_equal_to_the_clients_is_still_the_servers(dispatch_with: Dispatch) -> None:
    # Small ints are one object, so the hook's ``7`` is the client's ``7``: the
    # server's value is told by where it came from, never by identity.
    dispatch_with(
        _spec(_IdIn),
        user=None,
        params={"tenant": 7},
        view_hooks=ViewHooks(extra_kwargs={"tenant": 7}),
    )

    assert SEEN == {"tenant": 7, "data": {"tenant": 7}}


@_CORES
@pytest.mark.parametrize(
    ("spec_kwargs", "hooks"),
    [
        ({"input_data": lambda: {"tenant": "server"}}, ViewHooks()),
        ({}, ViewHooks(input_data={"tenant": "server"})),
    ],
    ids=["the-specs", "a-view-hooks"],
)
def test_input_data_for_a_hidden_field_reaches_the_parameter(
    dispatch_with: Dispatch, spec_kwargs: dict[str, Any], hooks: ViewHooks
) -> None:
    # 0.57.0 handed ``input_data``'s value to such a parameter under a spreading
    # binding and its default under ``BUNDLE``; it is the server's either way.
    dispatch_with(_spec(**spec_kwargs), user=None, params={"tenant": "client"}, view_hooks=hooks)

    assert SEEN == {"tenant": "server", "data": {"tenant": "server"}}


@_CORES
def test_a_hooks_value_outranks_a_key_a_validate_adds_for_a_hidden_parameter(
    dispatch_with: Dispatch,
) -> None:
    dispatch_with(
        _spec(_AddsTenantIn),
        user=None,
        params={"title": "t"},
        view_hooks=ViewHooks(extra_kwargs={"tenant": "hook"}),
    )

    assert SEEN == {"tenant": "hook", "data": {"title": "t", "tenant": "validate-added"}}


@_CORES
def test_input_data_naming_no_field_keeps_nothing(dispatch_with: Dispatch) -> None:
    dispatch_with(
        _spec(input_data=lambda: {"region": "server"}),
        user=None,
        params={"tenant": "client"},
    )

    assert SEEN == {"tenant": "server-default", "data": {"tenant": "client"}}


@_CORES
def test_a_server_value_for_a_flattened_field_keeps_none_of_the_keys_it_spreads(
    dispatch_with: Dispatch,
) -> None:
    # The flattened field's own value is spread into keys of the serializer's
    # own, none of which the server named, so the parameter keeps its default,
    # as 0.57.0 handed it.
    dispatch_with(
        _spec(_FlattenedIn),
        user=None,
        params={"title": "t", "nested": {"tenant": "client"}},
        view_hooks=ViewHooks(input_data={"nested": {"tenant": "server"}}),
    )

    assert SEEN == {"tenant": "server-default", "data": {"title": "t", "tenant": "server"}}


# --- a kernel call inside the service is its own ----------------------------

_INNER = _spec(permission_classes=[])


def _calls_the_kernel(*, data: Any) -> None:
    principal = AnonymousUser()
    compiled = kernel_spec(_INNER)
    dispatch(
        compiled,
        principal=principal,
        arguments={"tenant": "inner-client"},
        grant=Grant(compiled, principal),
    )


@_CORES
def test_a_kernel_call_inside_the_service_takes_none_of_the_outer_calls_server_values(
    dispatch_with: Dispatch,
) -> None:
    outer = ServiceSpec(service=_calls_the_kernel, input_serializer=_TenantIn, atomic=False)

    dispatch_with(
        outer,
        user=None,
        params={"tenant": "client"},
        view_hooks=ViewHooks(input_data={"tenant": "server"}),
    )

    assert SEEN == {"tenant": "server-default", "data": {"tenant": "inner-client"}}
