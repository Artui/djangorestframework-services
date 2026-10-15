"""The server's value for an input field outranks a client value reaching the same key another way.

A view hook's, the spec's ``kwargs`` provider's or ``input_data``'s value for a
field of the input serializer is written into that field and validated by it.
DRF then builds ``validated_data`` field by field, so a later field with the
same ``source``, a ``source="*"`` nested serializer carrying a key of that name,
or a ``validate()`` copying another field into it, would put the client's value
back under the key. What the field validates in its own turn of DRF's field
loop is the parameter's and is laid back over its key, so the service's
parameter, its ``data`` and the bound serializer's ``validated_data`` all hold
the server's value, as a plain field gives it. The field's validation run again
on another value, a ``validate()`` dropping the key, a dataclass instance and a
dotted or flattened ``source`` each have tests of their own below, as do the
two shapes left as built: a dataclass ``data`` and a ``many`` spec's rows.

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
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services import (
    DispatchResult,
    NotClientInput,
    ServiceCreateView,
    ServiceSpec,
    ViewHooks,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.types.argument_binding import ArgumentBinding
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


_BINDINGS = pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.AUTO, ArgumentBinding.BUNDLE, ArgumentBinding.SPREAD_AUTHOR_WINS],
    ids=["auto", "bundle", "spread"],
)


@pytest.mark.django_db
@_CORES
@_SHAPE
@_ROUTE
@_PARAMETER
@_BINDINGS
def test_the_servers_value_outranks_a_second_route_off_http(
    dispatch: Dispatch,
    serializer: Any,
    body: dict[str, Any],
    spec_kwargs: dict[str, Any],
    hooks: ViewHooks | None,
    parameter: Callable[..., Callable[..., None]],
    binding: ArgumentBinding,
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=parameter(seen), input_serializer=serializer, atomic=False, **spec_kwargs
    )

    dispatch(spec, params=body, view_hooks=hooks, argument_binding=binding)

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


def _shouted() -> dict[str, Any]:
    return {"tenant": "SERVER"}


class _Lowers(serializers.Serializer):
    tenant = serializers.CharField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "tenant" in attrs:
            attrs["tenant"] = attrs["tenant"].lower()
        return attrs


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(
    ("spec_kwargs", "hooks"),
    [
        ({}, ViewHooks(extra_kwargs={"tenant": "SERVER"})),
        ({}, ViewHooks(input_data={"tenant": "SERVER"})),
        ({"kwargs": _shouted}, None),
        ({"input_data": _shouted}, None),
    ],
    ids=["hook-extra", "hook-input-data", "spec-kwargs", "spec-input-data"],
)
def test_a_validate_rewrite_of_the_servers_own_value_is_laid_over(
    dispatch: Dispatch, spec_kwargs: dict[str, Any], hooks: ViewHooks | None
) -> None:
    """``validate()`` cannot tell the server's value from a client's, and neither can the lay-back.

    What holds the server's value against a ``validate()`` copying a client's
    over it also holds it against a ``validate()`` rewriting the server's own,
    so the service sees the value as its field validated it, not as
    ``validate()`` left it.
    """
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_hidden(seen), input_serializer=_Lowers, atomic=False, **spec_kwargs)

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


# A ``validate()`` or an earlier field's ``validate_<field>`` reusing the server
# field's own validation on a client's value: what the field validated in its
# own turn is the server's, whatever else ran its validation.


class _AfterByMethod(serializers.Serializer):
    """``validate()`` runs ``validate_tenant`` again, on another field's value."""

    tenant = serializers.CharField(required=False)
    other = serializers.CharField(required=False)

    def validate_tenant(self, value: str) -> str:
        return value.lower()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "other" in attrs:
            attrs["tenant"] = self.validate_tenant(attrs["other"])
        return attrs


class _AfterByField(serializers.Serializer):
    """``validate()`` runs the field's ``run_validation`` again, on another field's value."""

    tenant = serializers.CharField(required=False)
    other = serializers.CharField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "other" in attrs:
            attrs["tenant"] = self.fields["tenant"].run_validation(attrs["other"])
        return attrs


class _AfterByTurn(serializers.Serializer):
    """``validate()`` repeats the field loop's own calls for ``tenant`` over another field's value."""

    tenant = serializers.CharField(required=False)
    other = serializers.CharField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "other" in attrs:
            field = self.fields["tenant"]
            attrs["tenant"] = field.run_validation(field.get_value({"tenant": attrs["other"]}))
        return attrs


class _BeforeByMethod(serializers.Serializer):
    """An earlier field checks its value with ``validate_tenant``, before ``tenant``'s turn."""

    other = serializers.CharField(required=False)
    tenant = serializers.CharField(required=False)

    def validate_tenant(self, value: str) -> str:
        return value.lower()

    def validate_other(self, value: str) -> str:
        self.validate_tenant(value)
        return value


class _BeforeByField(serializers.Serializer):
    """An earlier field checks its value with ``tenant``'s ``run_validation``, before its turn."""

    other = serializers.CharField(required=False)
    tenant = serializers.CharField(required=False)

    def validate_other(self, value: str) -> str:
        self.fields["tenant"].run_validation(value)
        return value


_REUSES = pytest.mark.parametrize(
    "serializer",
    [_AfterByMethod, _AfterByField, _AfterByTurn, _BeforeByMethod, _BeforeByField],
    ids=[
        "after-by-method",
        "after-by-field",
        "after-by-turn",
        "before-by-method",
        "before-by-field",
    ],
)


@pytest.mark.django_db
@_CORES
@_ROUTE
@_REUSES
@_PARAMETER
@_BINDINGS
def test_a_reuse_of_the_fields_validation_is_not_the_servers_value_off_http(
    dispatch: Dispatch,
    serializer: Any,
    spec_kwargs: dict[str, Any],
    hooks: ViewHooks | None,
    parameter: Callable[..., Callable[..., None]],
    binding: ArgumentBinding,
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=parameter(seen), input_serializer=serializer, atomic=False, **spec_kwargs
    )

    dispatch(spec, params={"other": "EVIL"}, view_hooks=hooks, argument_binding=binding)

    assert seen == _SERVER


@pytest.mark.django_db
@_REUSES
@_HTTP_ROUTE
@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_a_reuse_of_the_fields_validation_is_not_the_servers_value_over_http(
    serializer: Any, spec_kwargs: dict[str, Any], view_for: Callable[..., Any], fmt: str
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_hidden(seen), input_serializer=serializer, atomic=False, **spec_kwargs
    )

    response = view_for(spec).as_view()(_factory.post("/", {"other": "EVIL"}, format=fmt))

    assert response.status_code == 204, response.data
    assert seen == _SERVER


@pytest.mark.django_db(transaction=True)
@_REUSES
@pytest.mark.parametrize(("spec_kwargs", "hooks"), _LIVE.values(), ids=_LIVE.keys())
async def test_a_reuse_of_the_fields_validation_is_not_the_servers_value_on_a_live_async_view(
    serializer: Any, spec_kwargs: dict[str, Any], hooks: ViewHooks | None
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_hidden(seen), input_serializer=serializer, atomic=False, **spec_kwargs
    )
    view = await sync_to_async(_live_view, thread_sensitive=True)({"other": "EVIL"}, "json")

    await adispatch_spec(
        spec, user=None, params=view.request.data, request=view.request, view=view, view_hooks=hooks
    )

    assert seen == _SERVER


# A dataclass input whose ``validate()`` copies a client value over the server's
# field. The instance is the call's ``data`` as the serializer built it; the
# parameter is the field's own value.


@dataclasses.dataclass
class _CopiedInput:
    tenant: str = "unset"
    other: str = ""


class _CopiesIntoTheInstance(DataclassSerializer):
    class Meta:
        dataclass = _CopiedInput

    def validate(self, attrs: Any) -> Any:
        # A DataclassSerializer hands ``validate()`` the instance it built.
        if attrs.other:
            attrs.tenant = attrs.other
        return attrs


def _dataclass_service(seen: dict[str, Any], *, hidden: bool) -> Callable[..., None]:
    if hidden:

        def service(*, data: Any, tenant: Annotated[str, NotClientInput] = "default") -> None:
            seen.update(tenant=tenant, data=data.tenant)

        return service

    def plain(*, data: Any, tenant: str = "default") -> None:
        seen.update(tenant=tenant, data=data.tenant)

    return plain


@pytest.mark.django_db
@_CORES
@_ROUTE
@pytest.mark.parametrize("hidden", [True, False], ids=["hidden", "plain"])
def test_a_dataclass_validate_copy_never_reaches_the_parameter_off_http(
    dispatch: Dispatch, spec_kwargs: dict[str, Any], hooks: ViewHooks | None, hidden: bool
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_dataclass_service(seen, hidden=hidden),
        input_serializer=_CopiesIntoTheInstance,
        atomic=False,
        **spec_kwargs,
    )

    dispatch(spec, params={"other": "evil"}, view_hooks=hooks)

    assert seen == {"tenant": "server", "data": "evil"}


@pytest.mark.django_db
@_HTTP_ROUTE
def test_a_dataclass_validate_copy_never_reaches_the_parameter_over_http(
    spec_kwargs: dict[str, Any], view_for: Callable[..., Any]
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_dataclass_service(seen, hidden=True),
        input_serializer=_CopiesIntoTheInstance,
        atomic=False,
        **spec_kwargs,
    )

    response = view_for(spec).as_view()(_factory.post("/", {"other": "evil"}, format="json"))

    assert response.status_code == 204, response.data
    assert seen == {"tenant": "server", "data": "evil"}


# ``input_data`` naming a read-only field: the field never takes the value, so it
# holds no server value, and the client's value written under its key by another
# route stays in ``data`` alone.


class _ReadOnlyAlias(serializers.Serializer):
    tenant = serializers.CharField(read_only=True)
    alias = serializers.CharField(source="tenant", required=False)


class _ReadOnlyCopies(serializers.Serializer):
    tenant = serializers.CharField(read_only=True)
    other = serializers.CharField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "other" in attrs:
            attrs["tenant"] = attrs["other"]
        return attrs


_READ_ONLY = pytest.mark.parametrize(
    ("serializer", "body"),
    [(_ReadOnlyAlias, {"alias": "evil"}), (_ReadOnlyCopies, {"other": "evil"})],
    ids=["alias", "validate"],
)
_INPUT_DATA = {
    "hook-input-data": ({}, ViewHooks(input_data={"tenant": "server"})),
    "spec-input-data": ({"input_data": _server}, None),
}
_CLIENTS = {"tenant": "default", "data": "evil", "validated": "evil"}


@pytest.mark.django_db
@_CORES
@_READ_ONLY
@pytest.mark.parametrize(("spec_kwargs", "hooks"), _INPUT_DATA.values(), ids=_INPUT_DATA.keys())
def test_input_data_for_a_read_only_field_holds_no_server_value_off_http(
    dispatch: Dispatch,
    serializer: Any,
    body: dict[str, Any],
    spec_kwargs: dict[str, Any],
    hooks: ViewHooks | None,
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_hidden(seen), input_serializer=serializer, atomic=False, **spec_kwargs
    )

    dispatch(spec, params=body, view_hooks=hooks)

    assert seen == _CLIENTS


@pytest.mark.django_db
@_READ_ONLY
def test_input_data_for_a_read_only_field_holds_no_server_value_over_http(
    serializer: Any, body: dict[str, Any]
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_hidden(seen), input_serializer=serializer, atomic=False, input_data=_server
    )

    response = _plain_view(spec).as_view()(_factory.post("/", body, format="json"))

    assert response.status_code == 204, response.data
    assert seen == _CLIENTS


# A ``validate()`` that drops the server field's key: the parameter is still
# handed the field's value, as 0.57.0 handed a hook's.


class _Drops(serializers.Serializer):
    tenant = serializers.CharField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs.pop("tenant", None)
        return attrs


def _keeps(seen: dict[str, Any], *, hidden: bool) -> Callable[..., None]:
    if hidden:

        def service(*, data: Any, tenant: Annotated[str, NotClientInput] = "default") -> None:
            seen.update(tenant=tenant, data=dict(data))

        return service

    def plain(*, data: Any, tenant: str = "default") -> None:
        seen.update(tenant=tenant, data=dict(data))

    return plain


@pytest.mark.django_db
@_CORES
@_ROUTE
@pytest.mark.parametrize("hidden", [True, False], ids=["hidden", "plain"])
def test_a_key_validate_drops_still_reaches_the_parameter_off_http(
    dispatch: Dispatch, spec_kwargs: dict[str, Any], hooks: ViewHooks | None, hidden: bool
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_keeps(seen, hidden=hidden), input_serializer=_Drops, atomic=False, **spec_kwargs
    )

    dispatch(spec, params={"tenant": "c1"}, view_hooks=hooks)

    assert seen == {"tenant": "server", "data": {}}


@pytest.mark.django_db
@_HTTP_ROUTE
def test_a_key_validate_drops_still_reaches_the_parameter_over_http(
    spec_kwargs: dict[str, Any], view_for: Callable[..., Any]
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_keeps(seen, hidden=True), input_serializer=_Drops, atomic=False, **spec_kwargs
    )

    response = view_for(spec).as_view()(_factory.post("/", {"tenant": "c1"}, format="json"))

    assert response.status_code == 204, response.data
    assert seen == {"tenant": "server", "data": {}}


# A field with a dotted ``source`` has no top-level key of its name, so the
# server's value for it is handed to the parameter named like the field, as
# 0.57.0 handed a hook's by the name the hook gave.


class _Scoped(serializers.Serializer):
    tenant = serializers.CharField(source="org.tenant", required=False)


@pytest.mark.django_db
@_CORES
@_ROUTE
@pytest.mark.parametrize("hidden", [True, False], ids=["hidden", "plain"])
def test_a_dotted_fields_server_value_reaches_the_parameter_named_like_the_field_off_http(
    dispatch: Dispatch, spec_kwargs: dict[str, Any], hooks: ViewHooks | None, hidden: bool
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_keeps(seen, hidden=hidden), input_serializer=_Scoped, atomic=False, **spec_kwargs
    )

    dispatch(spec, params={"tenant": "c1"}, view_hooks=hooks)

    assert seen == {"tenant": "server", "data": {"org": {"tenant": "server"}}}


@pytest.mark.django_db
@_HTTP_ROUTE
def test_a_dotted_fields_server_value_reaches_the_parameter_named_like_the_field_over_http(
    spec_kwargs: dict[str, Any], view_for: Callable[..., Any]
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_keeps(seen, hidden=False), input_serializer=_Scoped, atomic=False, **spec_kwargs
    )

    response = view_for(spec).as_view()(_factory.post("/", {"tenant": "c1"}, format="json"))

    assert response.status_code == 204, response.data
    assert seen == {"tenant": "server", "data": {"org": {"tenant": "server"}}}


class _Flattens(serializers.Serializer):
    nested = _InnerTenant(source="*", required=False)


def _takes_nested(seen: dict[str, Any], *, hidden: bool) -> Callable[..., None]:
    if hidden:

        def service(*, data: Any, nested: Annotated[Any, NotClientInput] = "default") -> None:
            seen.update(nested=nested, data=dict(data))

        return service

    def plain(*, data: Any, nested: Any = "default") -> None:
        seen.update(nested=nested, data=dict(data))

    return plain


_NESTED_SERVER = {"tenant": "server"}


def _nested_server() -> dict[str, Any]:
    return {"nested": _NESTED_SERVER}


_NESTED_ROUTE = pytest.mark.parametrize(
    ("spec_kwargs", "hooks"),
    [
        ({}, ViewHooks(extra_kwargs={"nested": _NESTED_SERVER})),
        ({}, ViewHooks(input_data={"nested": _NESTED_SERVER})),
        ({"kwargs": _nested_server}, None),
        ({"input_data": _nested_server}, None),
    ],
    ids=_OFF_HTTP.keys(),
)


@pytest.mark.django_db
@_CORES
@_NESTED_ROUTE
@pytest.mark.parametrize("hidden", [True, False], ids=["hidden", "plain"])
def test_a_flattened_fields_server_value_reaches_the_parameter_named_like_the_field_off_http(
    dispatch: Dispatch, spec_kwargs: dict[str, Any], hooks: ViewHooks | None, hidden: bool
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_takes_nested(seen, hidden=hidden),
        input_serializer=_Flattens,
        atomic=False,
        **spec_kwargs,
    )

    dispatch(spec, params={"nested": {"tenant": "c1"}}, view_hooks=hooks)

    assert seen == {"nested": _NESTED_SERVER, "data": _NESTED_SERVER}


@pytest.mark.django_db
@_CORES
def test_a_flattened_fields_client_value_reaches_no_parameter_of_its_name(
    dispatch: Dispatch,
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_takes_nested(seen, hidden=False), input_serializer=_Flattens, atomic=False
    )

    dispatch(spec, params={"nested": {"tenant": "c1"}})

    assert seen == {"nested": "default", "data": {"tenant": "c1"}}


@pytest.mark.django_db
@_CORES
def test_a_dotted_fields_client_value_is_handed_by_its_source_alone(dispatch: Dispatch) -> None:
    """With no server value the field is the client's, handed on under its ``source`` as before."""
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_keeps(seen, hidden=False), input_serializer=_Scoped, atomic=False)

    dispatch(spec, params={"tenant": "c1"})

    assert seen == {"tenant": "default", "data": {"org": {"tenant": "c1"}}}


# Two author values for one field: ``input_data``'s is the field's.


def _authored() -> dict[str, Any]:
    return {"tenant": "authored"}


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(
    ("spec_kwargs", "hooks"),
    [
        ({"input_data": _server}, ViewHooks(extra_kwargs={"tenant": "authored"})),
        ({"input_data": _server, "kwargs": _authored}, None),
    ],
    ids=["hook-extra", "spec-kwargs"],
)
@_PARAMETER
def test_input_data_outranks_an_authors_value_for_the_same_field(
    dispatch: Dispatch,
    spec_kwargs: dict[str, Any],
    hooks: ViewHooks | None,
    parameter: Callable[..., Callable[..., None]],
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=parameter(seen), input_serializer=_Alias, atomic=False, **spec_kwargs
    )

    dispatch(spec, params={}, view_hooks=hooks)

    assert seen == _SERVER


# A ``HiddenField`` takes no input; ``input_data`` naming it hands the
# parameter the field's own value, which is the server's.


class _HiddenTenant(serializers.Serializer):
    tenant = serializers.HiddenField(default="field-default")
    title = serializers.CharField(required=False)


@pytest.mark.django_db
@_CORES
@_BINDINGS
def test_input_data_naming_a_hidden_field_hands_the_parameter_the_fields_value(
    dispatch: Dispatch, binding: ArgumentBinding
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_keeps(seen, hidden=True),
        input_serializer=_HiddenTenant,
        atomic=False,
        input_data=_server,
    )

    dispatch(spec, params={"tenant": "evil"}, argument_binding=binding)

    assert seen == {"tenant": "field-default", "data": {"tenant": "field-default"}}


# A ``many`` spec's rows are handed on as its list serializer built them: the
# server's value for a row's field is not laid back over a second route.


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(
    ("serializer", "row"),
    [(_Alias, {"alias": "evil"}), (_Copies, {"other": "evil"})],
    ids=["alias", "validate"],
)
def test_a_bulk_row_is_handed_on_as_its_serializer_built_it(
    dispatch: Dispatch, serializer: Any, row: dict[str, Any]
) -> None:
    seen: dict[str, Any] = {}

    def service(*, data: Any) -> None:
        seen["data"] = [item["tenant"] for item in data]

    spec = ServiceSpec(
        service=service, input_serializer=serializer, many=True, atomic=False, input_data=_server
    )

    dispatch(spec, params=[row])

    assert seen == {"data": ["evil"]}


@pytest.mark.django_db
def test_a_bulk_row_is_handed_on_as_its_serializer_built_it_over_http() -> None:
    seen: dict[str, Any] = {}

    def service(*, data: Any) -> None:
        seen["data"] = [item["tenant"] for item in data]

    spec = ServiceSpec(
        service=service, input_serializer=_Copies, many=True, atomic=False, input_data=_server
    )

    response = _plain_view(spec).as_view()(_factory.post("/", [{"other": "evil"}], format="json"))

    assert response.status_code == 201, response.data
    assert seen == {"data": ["evil"]}
