"""The server's value for an input field outranks a client value reaching the same key another way.

A view hook's, the spec's ``kwargs`` provider's or ``input_data``'s value for a
field of the input serializer is written into that field and validated by it.
DRF then builds ``validated_data`` field by field, so a later field with the
same ``source``, a ``source="*"`` nested serializer carrying a key of that name,
or a ``validate()`` copying another field into it, would put the client's value
back under the key. The field's own validated value is laid back over its key,
so the service's parameter, its ``data`` and the bound serializer's
``validated_data`` all hold the server's value, as a plain field gives it.

0.57.0 handed a hook's or the provider's value to the parameter over whatever
the client's input became, and kept the client's value in ``data``; its
``input_data`` value never reached a parameter (the parameter kept its
default). These tests pin the server's value in all three places for every
route; where that departs from 0.57.0, the changelog says so.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Annotated, Any

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework import serializers
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DispatchResult,
    NotClientInput,
    ServiceCreateView,
    ServiceSpec,
    ViewHooks,
    adispatch_spec,
    dispatch_spec,
)
from tests.testapp.models import Author

_factory = APIRequestFactory()


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


class _Alias(serializers.Serializer):
    """A second field writing the same ``source``, after the first."""

    tenant = serializers.CharField(required=False)
    alias = serializers.CharField(source="tenant", required=False)


class _InnerTenant(serializers.Serializer):
    tenant = serializers.CharField(required=False)


class _Star(serializers.Serializer):
    """A ``source="*"`` nested serializer whose own ``tenant`` lands at the top level."""

    tenant = serializers.CharField(required=False)
    nested = _InnerTenant(source="*", required=False)


class _Copies(serializers.Serializer):
    """A ``validate()`` copying another field over ``tenant``."""

    tenant = serializers.CharField(required=False)
    other = serializers.CharField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "other" in attrs:
            attrs["tenant"] = attrs["other"]
        return attrs


_SHAPES = {
    "alias": (_Alias, {"tenant": "c1", "alias": "evil"}),
    "star": (_Star, {"tenant": "c1", "nested": {"tenant": "evil"}}),
    "validate": (_Copies, {"other": "evil"}),
}
_SHAPE = pytest.mark.parametrize(("serializer", "body"), _SHAPES.values(), ids=_SHAPES.keys())


def _hidden(seen: dict[str, Any]) -> Callable[..., None]:
    def service(
        *, data: Any, serializer: Any, tenant: Annotated[str, NotClientInput] = "default"
    ) -> None:
        seen.update(
            tenant=tenant, data=data["tenant"], validated=serializer.validated_data["tenant"]
        )

    return service


def _plain(seen: dict[str, Any]) -> Callable[..., None]:
    def service(*, data: Any, serializer: Any, tenant: str = "default") -> None:
        seen.update(
            tenant=tenant, data=data["tenant"], validated=serializer.validated_data["tenant"]
        )

    return service


_PARAMETER = pytest.mark.parametrize("parameter", [_hidden, _plain], ids=["hidden", "plain"])
_SERVER = {"tenant": "server", "data": "server", "validated": "server"}


def _server() -> dict[str, Any]:
    return {"tenant": "server"}


# The four routes off HTTP: a view hook's extra and its ``input_data``, and the
# spec's ``kwargs`` provider and ``input_data`` provider.
_OFF_HTTP = {
    "hook-extra": ({}, ViewHooks(extra_kwargs={"tenant": "server"})),
    "hook-input-data": ({}, ViewHooks(input_data={"tenant": "server"})),
    "spec-kwargs": ({"kwargs": _server}, None),
    "spec-input-data": ({"input_data": _server}, None),
}
_ROUTE = pytest.mark.parametrize(("spec_kwargs", "hooks"), _OFF_HTTP.values(), ids=_OFF_HTTP.keys())


@pytest.mark.django_db
@_CORES
@_SHAPE
@_ROUTE
@_PARAMETER
def test_the_servers_value_outranks_a_second_route_off_http(
    dispatch: Dispatch,
    serializer: Any,
    body: dict[str, Any],
    spec_kwargs: dict[str, Any],
    hooks: ViewHooks | None,
    parameter: Callable[..., Callable[..., None]],
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=parameter(seen), input_serializer=serializer, atomic=False, **spec_kwargs
    )

    dispatch(spec, params=body, view_hooks=hooks)

    assert seen == _SERVER


def _hook_view(spec: ServiceSpec[Any, Any, Any]) -> Any:
    class _View(ServiceCreateView):
        permission_classes = []

        def get_service_kwargs(self) -> dict[str, Any]:
            return {"tenant": "server"}

    _View.spec = spec
    return _View


def _plain_view(spec: ServiceSpec[Any, Any, Any]) -> Any:
    return type("_View", (ServiceCreateView,), {"permission_classes": [], "spec": spec})


# Over HTTP: the view's ``get_service_kwargs``, and the spec's two providers.
_OVER_HTTP = {
    "get-service-kwargs": ({}, _hook_view),
    "spec-kwargs": ({"kwargs": _server}, _plain_view),
    "spec-input-data": ({"input_data": _server}, _plain_view),
}
_HTTP_ROUTE = pytest.mark.parametrize(
    ("spec_kwargs", "view_for"), _OVER_HTTP.values(), ids=_OVER_HTTP.keys()
)


@pytest.mark.django_db
@_SHAPE
@_HTTP_ROUTE
@_PARAMETER
def test_the_servers_value_outranks_a_second_route_over_http(
    serializer: Any,
    body: dict[str, Any],
    spec_kwargs: dict[str, Any],
    view_for: Callable[..., Any],
    parameter: Callable[..., Callable[..., None]],
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=parameter(seen), input_serializer=serializer, atomic=False, **spec_kwargs
    )

    response = view_for(spec).as_view()(_factory.post("/", body, format="json"))

    assert response.status_code == 204, response.data
    assert seen == _SERVER


@pytest.mark.django_db
@_HTTP_ROUTE
def test_the_servers_value_outranks_a_second_route_in_a_form_body(
    spec_kwargs: dict[str, Any], view_for: Callable[..., Any]
) -> None:
    """A multipart body is bound as the ``QueryDict`` it parsed to, the server's value set on it."""
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_hidden(seen), input_serializer=_Alias, atomic=False, **spec_kwargs)

    response = view_for(spec).as_view()(
        _factory.post("/", {"tenant": "c1", "alias": "evil"}, format="multipart")
    )

    assert response.status_code == 204, response.data
    assert seen == _SERVER


def _live_view(body: dict[str, Any], fmt: str) -> ServiceCreateView:
    """A live view over a parsed request, as the async views hand ``adispatch_spec`` one."""
    view = ServiceCreateView()
    view.request = Request(
        _factory.post("/", body, format=fmt), parsers=[JSONParser(), MultiPartParser()]
    )
    view.request.user = AnonymousUser()
    view.kwargs = {}
    view.format_kwarg = None
    return view


# The same three routes as a live view resolves them: a ``get_service_kwargs``
# value reaches the core as a view hook's extra.
_LIVE = {
    "get-service-kwargs": ({}, ViewHooks(extra_kwargs={"tenant": "server"})),
    "spec-kwargs": ({"kwargs": _server}, None),
    "spec-input-data": ({"input_data": _server}, None),
}


# A form body carries no nested object, so the ``source="*"`` shape is sent as JSON alone.
_LIVE_BODIES = [
    pytest.param(serializer, body, fmt, id=f"{shape}-{fmt}")
    for shape, (serializer, body) in _SHAPES.items()
    for fmt in ("json", "multipart")
    if not (shape == "star" and fmt == "multipart")
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("serializer", "body", "fmt"), _LIVE_BODIES)
@pytest.mark.parametrize(("spec_kwargs", "hooks"), _LIVE.values(), ids=_LIVE.keys())
async def test_the_servers_value_outranks_a_second_route_over_http_on_the_async_core(
    serializer: Any,
    body: dict[str, Any],
    spec_kwargs: dict[str, Any],
    hooks: ViewHooks | None,
    fmt: str,
) -> None:
    """A live view's body is bound as the request parsed it, on the async core too."""
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_hidden(seen), input_serializer=serializer, atomic=False, **spec_kwargs
    )
    view = await sync_to_async(_live_view, thread_sensitive=True)(body, fmt)

    await adispatch_spec(
        spec, user=None, params=view.request.data, request=view.request, view=view, view_hooks=hooks
    )

    assert seen == _SERVER


class _Shouting(_Alias):
    def validate_tenant(self, value: str) -> str:
        return value.upper()


@pytest.mark.django_db
@_CORES
def test_the_value_laid_back_is_the_fields_validate_method_s(dispatch: Dispatch) -> None:
    """The field's value as ``validated_data`` would have held it: after ``validate_<field>``."""
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_hidden(seen), input_serializer=_Shouting, atomic=False)

    dispatch(
        spec,
        params={"alias": "evil"},
        view_hooks=ViewHooks(extra_kwargs={"tenant": "server"}),
    )

    assert seen == {"tenant": "SERVER", "data": "SERVER", "validated": "SERVER"}


class _Lowers(serializers.Serializer):
    tenant = serializers.CharField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "tenant" in attrs:
            attrs["tenant"] = attrs["tenant"].lower()
        return attrs


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(
    "hooks",
    [
        ViewHooks(extra_kwargs={"tenant": "SERVER"}),
        ViewHooks(input_data={"tenant": "SERVER"}),
    ],
    ids=["extra", "input-data"],
)
def test_a_validate_rewrite_of_the_servers_own_value_is_laid_over(
    dispatch: Dispatch, hooks: ViewHooks
) -> None:
    """``validate()`` cannot tell the server's value from a client's, and neither can the lay-back.

    What holds the server's value against a ``validate()`` copying a client's
    over it also holds it against a ``validate()`` rewriting the server's own,
    so the service sees the value as its field validated it, not as
    ``validate()`` left it.
    """
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_hidden(seen), input_serializer=_Lowers, atomic=False)

    dispatch(spec, params={"tenant": "client"}, view_hooks=hooks)

    assert seen == {"tenant": "SERVER", "data": "SERVER", "validated": "SERVER"}


class _AuthorIn(serializers.Serializer):
    author = serializers.PrimaryKeyRelatedField(queryset=Author.objects.all(), required=False)
    writer = serializers.PrimaryKeyRelatedField(
        source="author", queryset=Author.objects.all(), required=False
    )


@pytest.mark.django_db
@_CORES
def test_the_value_laid_back_is_the_fields_validated_value(dispatch: Dispatch) -> None:
    """A primary key the server gives arrives as the row its field resolves it to."""
    mine = Author.objects.create(name="mine")
    theirs = Author.objects.create(name="theirs")
    seen: dict[str, Any] = {}

    def service(*, data: Any, author: Annotated[Any, NotClientInput] = None) -> None:
        seen.update(author=author, data=data["author"])

    spec = ServiceSpec(service=service, input_serializer=_AuthorIn, atomic=False)

    dispatch(
        spec,
        params={"writer": theirs.pk},
        view_hooks=ViewHooks(extra_kwargs={"author": mine.pk}),
    )

    assert seen == {"author": mine, "data": mine}


class _Dotted(serializers.Serializer):
    """Two fields writing one nested ``source``."""

    tenant = serializers.CharField(source="scope.inner.tenant", required=False)
    alias = serializers.CharField(source="scope.inner.tenant", required=False)


@pytest.mark.django_db
@_CORES
def test_a_dotted_source_is_laid_back_where_it_nests(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}

    def service(*, data: Any) -> None:
        seen["data"] = data

    spec = ServiceSpec(service=service, input_serializer=_Dotted, atomic=False)

    dispatch(
        spec, params={"alias": "evil"}, view_hooks=ViewHooks(extra_kwargs={"tenant": "server"})
    )

    assert seen["data"] == {"scope": {"inner": {"tenant": "server"}}}


class _DottedFlattened(_Dotted):
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs["scope"] = attrs["scope"]["inner"]["tenant"]
        return attrs


@pytest.mark.django_db
@_CORES
def test_a_dotted_source_a_validate_replaced_is_left_as_built(dispatch: Dispatch) -> None:
    """Where the nesting a dotted ``source`` names is no longer a mapping, nothing is written."""
    seen: dict[str, Any] = {}

    def service(*, data: Any) -> None:
        seen["data"] = data

    spec = ServiceSpec(service=service, input_serializer=_DottedFlattened, atomic=False)

    dispatch(
        spec, params={"alias": "evil"}, view_hooks=ViewHooks(extra_kwargs={"tenant": "server"})
    )

    assert seen["data"] == {"scope": "evil"}


class _Renames(serializers.Serializer):
    """A ``validate()`` that moves the field's value to a key of its own."""

    tenant = serializers.CharField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "tenant" in attrs:
            attrs["tenant_id"] = attrs.pop("tenant")
        return attrs


@pytest.mark.django_db
@_CORES
def test_a_key_validate_removed_is_not_put_back(dispatch: Dispatch) -> None:
    """Only a key the serializer still holds is overwritten: ``data`` keeps the serializer's shape."""
    seen: dict[str, Any] = {}

    def service(*, data: Any) -> None:
        seen["data"] = dict(data)

    spec = ServiceSpec(service=service, input_serializer=_Renames, atomic=False)

    dispatch(spec, params={"tenant": "c1"}, view_hooks=ViewHooks(extra_kwargs={"tenant": "server"}))

    assert seen["data"] == {"tenant_id": "server"}


@dataclasses.dataclass
class _TenantInput:
    tenant: str = "unset"


@pytest.mark.django_db
@_CORES
def test_a_dataclass_input_holds_the_servers_value_its_field_validated(dispatch: Dispatch) -> None:
    """A dataclass instance is the call's ``data`` as built; nothing is laid over it."""
    seen: dict[str, Any] = {}

    def service(*, data: Any, tenant: Annotated[str, NotClientInput] = "default") -> None:
        seen.update(tenant=tenant, data=data.tenant)

    spec = ServiceSpec(service=service, input_serializer=_TenantInput, atomic=False)

    dispatch(spec, params={"tenant": "c1"}, view_hooks=ViewHooks(extra_kwargs={"tenant": "server"}))

    assert seen == {"tenant": "server", "data": "server"}
