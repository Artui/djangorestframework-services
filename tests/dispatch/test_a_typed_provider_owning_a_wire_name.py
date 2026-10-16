"""A typed ``kwargs`` provider annotated to fill a field's wire name owns its ``source`` too.

``tenant = CharField(source="org")`` is one value under two names: the client
sends ``tenant``, the service is handed ``org``. A provider owning either name
owns the value, so declining it leaves both parameters their defaults and the
client's value in ``data`` alone, filling it hands the provider's value to both,
and a view hook naming either is dropped. A ``NotClientInput`` mark on the wire
name keeps the client's value out of the ``source``'s parameter the same way.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, TypedDict, cast

import pytest
from asgiref.sync import async_to_sync
from django.http import QueryDict
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ArgumentBinding,
    DispatchResult,
    NotClientInput,
    ServiceCreateView,
    ServiceSpec,
    ViewHooks,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.types.unset import UNSET

_factory = APIRequestFactory()


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])
_BINDINGS = pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.AUTO, ArgumentBinding.BUNDLE, ArgumentBinding.SPREAD_AUTHOR_WINS],
    ids=["auto", "bundle", "spread"],
)


def _form(values: dict[str, Any]) -> QueryDict:
    body = QueryDict(mutable=True)
    for name, value in values.items():
        body[name] = value
    return body


_BODY = pytest.mark.parametrize("body", [dict, _form], ids=["mapping", "form"])


class _SourcedIn(serializers.Serializer):
    tenant = serializers.CharField(source="org", required=False)


class _OwnsTheWireName(TypedDict):
    tenant: Any


class _OwnsTheSource(TypedDict):
    org: Any


def _provider(owns: str, value: Any) -> Callable[[], Any]:
    """A provider annotated to fill ``owns``, answering ``value`` for it."""
    keys = _OwnsTheWireName if owns == "tenant" else _OwnsTheSource

    def provider() -> Any:
        return {owns: value}

    provider.__annotations__ = {"return": keys}
    return provider


def _spec(seen: dict[str, Any], *, owns: str, value: Any) -> ServiceSpec[Any, Any, Any]:
    def service(*, data: Any, org: Any = "D-org", tenant: Any = "D-tenant") -> None:
        seen.update(org=org, tenant=tenant, data=dict(data))

    return ServiceSpec(
        service=service,
        input_serializer=_SourcedIn,
        kwargs=_provider(owns, value),
        atomic=False,
    )


_OWNS = pytest.mark.parametrize("owns", ["tenant", "org"], ids=["wire-name", "source"])
_DECLINED = {"org": "D-org", "tenant": "D-tenant", "data": {"org": "evil"}}


@pytest.mark.parametrize("fmt", ["json", "multipart"])
@_OWNS
def test_a_declined_owned_name_hands_neither_parameter_the_clients_value_over_http(
    fmt: str, owns: str
) -> None:
    seen: dict[str, Any] = {}
    spec = _spec(seen, owns=owns, value=UNSET)
    view = type("_View", (ServiceCreateView,), {"permission_classes": [], "spec": spec})

    response = view.as_view()(_factory.post("/", {"tenant": "evil"}, format=fmt))

    assert response.status_code == 204, response.data
    assert seen == _DECLINED


@_CORES
@_BINDINGS
@_BODY
@_OWNS
def test_a_declined_owned_name_hands_neither_parameter_the_clients_value_off_http(
    dispatch: Dispatch,
    binding: ArgumentBinding,
    body: Callable[[dict[str, Any]], Any],
    owns: str,
) -> None:
    seen: dict[str, Any] = {}

    dispatch(
        _spec(seen, owns=owns, value=UNSET),
        params=body({"tenant": "evil"}),
        argument_binding=binding,
    )

    assert seen == _DECLINED


# A value named by the field's wire name is handed under both names, and one
# named by its ``source`` under that alone, as a hook's value is.
_FILLED = {
    "tenant": {"org": "srv", "tenant": "srv", "data": {"org": "srv"}},
    "org": {"org": "srv", "tenant": "D-tenant", "data": {"org": "srv"}},
}


@_CORES
@_BINDINGS
@_BODY
@_OWNS
def test_a_filled_owned_name_is_the_providers_value(
    dispatch: Dispatch,
    binding: ArgumentBinding,
    body: Callable[[dict[str, Any]], Any],
    owns: str,
) -> None:
    seen: dict[str, Any] = {}

    dispatch(
        _spec(seen, owns=owns, value="srv"),
        params=body({"tenant": "evil"}),
        argument_binding=binding,
    )

    assert seen == _FILLED[owns]


@pytest.mark.parametrize("fmt", ["json", "multipart"])
@_OWNS
def test_a_filled_owned_name_is_the_providers_value_over_http(fmt: str, owns: str) -> None:
    seen: dict[str, Any] = {}
    spec = _spec(seen, owns=owns, value="srv")
    view = type("_View", (ServiceCreateView,), {"permission_classes": [], "spec": spec})

    response = view.as_view()(_factory.post("/", {"tenant": "evil"}, format=fmt))

    assert response.status_code == 204, response.data
    assert seen == _FILLED[owns]


@pytest.mark.parametrize("fmt", ["json", "multipart"])
@_OWNS
def test_a_hook_naming_the_other_name_of_an_owned_value_is_dropped_over_http(
    fmt: str, owns: str
) -> None:
    """A provider owning ``tenant`` owns ``org``, and the other way round."""
    seen: dict[str, Any] = {}
    other = "org" if owns == "tenant" else "tenant"
    view = type(
        "_View",
        (ServiceCreateView,),
        {
            "permission_classes": [],
            "spec": _spec(seen, owns=owns, value=UNSET),
            "get_service_kwargs": lambda self: {other: "hook"},
        },
    )

    response = view.as_view()(_factory.post("/", {"tenant": "evil"}, format=fmt))

    assert response.status_code == 204, response.data
    assert seen == _DECLINED


@_CORES
@_BINDINGS
@_BODY
@_OWNS
def test_a_hook_naming_the_other_name_of_an_owned_value_is_dropped_off_http(
    dispatch: Dispatch,
    binding: ArgumentBinding,
    body: Callable[[dict[str, Any]], Any],
    owns: str,
) -> None:
    seen: dict[str, Any] = {}
    other = "org" if owns == "tenant" else "tenant"

    dispatch(
        _spec(seen, owns=owns, value=UNSET),
        params=body({"tenant": "evil"}),
        argument_binding=binding,
        view_hooks=ViewHooks(extra_kwargs={other: "hook"}),
    )

    assert seen == _DECLINED


def _hidden_spec(seen: dict[str, Any]) -> ServiceSpec[Any, Any, Any]:
    def service(
        *, data: Any, org: Any = "D-org", tenant: Annotated[Any, NotClientInput] = "D-tenant"
    ) -> None:
        seen.update(org=org, tenant=tenant, data=dict(data))

    return ServiceSpec(service=service, input_serializer=_SourcedIn, atomic=False)


@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_a_client_value_for_a_hidden_wire_name_reaches_neither_parameter(fmt: str) -> None:
    seen: dict[str, Any] = {}
    view = type(
        "_View", (ServiceCreateView,), {"permission_classes": [], "spec": _hidden_spec(seen)}
    )

    response = view.as_view()(_factory.post("/", {"tenant": "evil"}, format=fmt))

    assert response.status_code == 204, response.data
    assert seen == _DECLINED


@_CORES
@_BINDINGS
@_BODY
def test_a_client_value_for_a_hidden_wire_name_reaches_neither_parameter_off_http(
    dispatch: Dispatch, binding: ArgumentBinding, body: Callable[[dict[str, Any]], Any]
) -> None:
    seen: dict[str, Any] = {}

    dispatch(_hidden_spec(seen), params=body({"tenant": "evil"}), argument_binding=binding)

    assert seen == _DECLINED


class _BesideItsInnerKey(serializers.Serializer):
    addr = serializers.CharField(source="loc.city", required=False)
    city = serializers.CharField(required=False)


@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_a_dotted_source_owns_the_key_it_nests_under_and_no_deeper_one(fmt: str) -> None:
    """``loc.city`` is nested under ``loc``: a field of its own named ``city`` stays the client's."""
    seen: dict[str, Any] = {}

    def service(
        *,
        data: Any,
        addr: Annotated[Any, NotClientInput] = "D-addr",
        loc: Any = "D-loc",
        city: Any = "D-city",
    ) -> None:
        seen.update(addr=addr, loc=loc, city=city)

    spec = ServiceSpec(service=service, input_serializer=_BesideItsInnerKey, atomic=False)
    view = type("_View", (ServiceCreateView,), {"permission_classes": [], "spec": spec})

    response = view.as_view()(_factory.post("/", {"addr": "x", "city": "client"}, format=fmt))

    assert response.status_code == 204, response.data
    assert seen == {"addr": "D-addr", "loc": "D-loc", "city": "client"}


class _NestedIn(serializers.Serializer):
    tenant = serializers.CharField(source="org.name", required=False)


@pytest.mark.parametrize("fmt", ["json", "multipart"])
@pytest.mark.parametrize("owner", ["provider", "marker"])
def test_an_owned_wire_name_over_a_dotted_source_owns_the_key_it_nests_under(
    fmt: str, owner: str
) -> None:
    seen: dict[str, Any] = {}

    def marked(
        *, data: Any, org: Any = "D-org", tenant: Annotated[Any, NotClientInput] = "D-tenant"
    ) -> None:
        seen.update(org=org, tenant=tenant, data=dict(data))

    def plain(*, data: Any, org: Any = "D-org", tenant: Any = "D-tenant") -> None:
        seen.update(org=org, tenant=tenant, data=dict(data))

    spec: ServiceSpec[Any, Any, Any] = (
        ServiceSpec(service=marked, input_serializer=_NestedIn, atomic=False)
        if owner == "marker"
        else ServiceSpec(
            service=plain,
            input_serializer=_NestedIn,
            kwargs=_provider("tenant", UNSET),
            atomic=False,
        )
    )
    view = type("_View", (ServiceCreateView,), {"permission_classes": [], "spec": spec})

    response = view.as_view()(_factory.post("/", {"tenant": "evil"}, format=fmt))

    assert response.status_code == 204, response.data
    assert seen == {"org": "D-org", "tenant": "D-tenant", "data": {"org": {"name": "evil"}}}


def _declines_the_wire_name_and_fills_the_source() -> _OwnsTheWireName:
    return cast(_OwnsTheWireName, {"tenant": UNSET, "org": "srv-org"})


def _keeping_the_source(seen: dict[str, Any]) -> ServiceSpec[Any, Any, Any]:
    def service(*, data: Any, org: Any = "D-org", tenant: Any = "D-tenant") -> None:
        seen.update(org=org, tenant=tenant, data=dict(data))

    return ServiceSpec(
        service=service,
        input_serializer=_SourcedIn,
        kwargs=_declines_the_wire_name_and_fills_the_source,
        atomic=False,
    )


# Owning the wire name owns its source, unless the provider gave the source a
# value of its own: that one it keeps, as a value named by the source is.
_KEPT = {"org": "srv-org", "tenant": "D-tenant", "data": {"org": "srv-org"}}


@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_a_source_the_provider_fills_is_kept_when_it_owns_the_wire_name_over_http(
    fmt: str,
) -> None:
    seen: dict[str, Any] = {}
    spec = _keeping_the_source(seen)
    view = type("_View", (ServiceCreateView,), {"permission_classes": [], "spec": spec})

    response = view.as_view()(_factory.post("/", {"tenant": "evil"}, format=fmt))

    assert response.status_code == 204, response.data
    assert seen == _KEPT


@_CORES
@_BODY
def test_a_source_the_provider_fills_is_kept_when_it_owns_the_wire_name_off_http(
    dispatch: Dispatch, body: Callable[[dict[str, Any]], Any]
) -> None:
    seen: dict[str, Any] = {}

    dispatch(_keeping_the_source(seen), params=body({"tenant": "evil"}))

    assert seen == _KEPT
