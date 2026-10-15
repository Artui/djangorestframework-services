"""The server's value for an input field is the value the edge put, however the field reads a body.

A view hook's, the spec's ``kwargs`` provider's or ``input_data``'s value for a
field is the server's. Where the field reads its value from more than its own
key of the body - a nested serializer, a dict or a nested ``many`` reading a
form body's ``v.tenant`` or ``v[0]tenant``, a list field falling back to them
when the server's list is empty, a field whose ``get_value`` reads another key -
what it reads from the body is the client's, so the value the edge put is the
one validated for the parameter and laid back into ``data``.

A field named like a seed of the call is the serializer's alone, read from the
body, and ``input_data`` naming one is written into that body as it was on
0.57.0, so ``data``, ``validated_data`` and the serializer's ``validate()`` all
hold the server's value, while the service's parameter of that name stays the
seed's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser, User
from django.http import QueryDict
from rest_framework import serializers
from rest_framework.fields import empty
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from rest_framework_services import (
    DispatchResult,
    NotClientInput,
    ServiceCreateView,
    ServiceSpec,
    ViewHooks,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS

_factory = APIRequestFactory()


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


def _form(values: dict[str, Any]) -> QueryDict:
    """A form body, as a multipart request's parser hands one on."""
    body = QueryDict(mutable=True)
    for name, value in values.items():
        body[name] = value
    return body


# A field named like a seed of the call: ``user``, the reserved one.


_VALIDATED: list[Any] = []


@pytest.fixture(autouse=True)
def _clear_validated() -> None:
    _VALIDATED.clear()


class _OwnedIn(serializers.Serializer):
    title = serializers.CharField()
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        _VALIDATED.append(attrs["user"])
        return attrs


def _owned(seen: dict[str, Any]) -> Callable[..., None]:
    def service(*, data: Any, serializer: Any, user: Any) -> None:
        seen.update(data=data["user"], validated=serializer.validated_data["user"], user=user)

    return service


def _owner(request: Any) -> dict[str, Any]:
    return {"user": request.user.pk}


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_input_data_for_a_field_named_user_outranks_the_clients_over_http(fmt: str) -> None:
    """The server's owner is the one validated, handed on and saved, as on 0.57.0."""
    alice = User.objects.create(username="alice")
    bob = User.objects.create(username="bob")
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_owned(seen), input_serializer=_OwnedIn, input_data=_owner, atomic=False
    )
    view = type("_View", (ServiceCreateView,), {"permission_classes": [], "spec": spec})
    request = _factory.post("/", {"title": "t", "user": bob.pk}, format=fmt)
    force_authenticate(request, user=alice)

    response = view.as_view()(request)

    assert response.status_code == 204, response.data
    assert seen == {"data": alice, "validated": alice, "user": alice}
    assert [alice] == _VALIDATED


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_the_clients_value_for_a_field_named_user_stays_the_fields_with_no_input_data(
    fmt: str,
) -> None:
    """Nothing the server put, so the field validates the client's value, as on 0.57.0."""
    alice = User.objects.create(username="alice")
    bob = User.objects.create(username="bob")
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_owned(seen), input_serializer=_OwnedIn, atomic=False)
    view = type("_View", (ServiceCreateView,), {"permission_classes": [], "spec": spec})
    request = _factory.post("/", {"title": "t", "user": bob.pk}, format=fmt)
    force_authenticate(request, user=alice)

    response = view.as_view()(request)

    assert response.status_code == 204, response.data
    assert seen == {"data": bob, "validated": bob, "user": alice}
    assert [bob] == _VALIDATED


# A field named like a seed a project registers.


class _TenantIn(serializers.Serializer):
    title = serializers.CharField()
    tenant = serializers.CharField()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        _VALIDATED.append(attrs["tenant"])
        return attrs


def _tenant(seen: dict[str, Any]) -> Callable[..., None]:
    def service(*, data: Any, serializer: Any, tenant: Any) -> None:
        seen.update(
            data=data["tenant"], validated=serializer.validated_data["tenant"], tenant=tenant
        )

    return service


def _server_tenant() -> dict[str, Any]:
    return {"tenant": "server"}


_SEED_ROUTE = pytest.mark.parametrize(
    ("spec_kwargs", "hooks"),
    [
        ({"input_data": _server_tenant}, None),
        ({}, ViewHooks(input_data={"tenant": "server"})),
    ],
    ids=["spec-input-data", "hook-input-data"],
)
_BODY = pytest.mark.parametrize("body", [dict, _form], ids=["mapping", "form"])


@pytest.mark.django_db
@_CORES
@_SEED_ROUTE
@_BODY
def test_input_data_for_a_field_named_like_a_registered_seed_outranks_the_clients_off_http(
    dispatch: Dispatch,
    spec_kwargs: dict[str, Any],
    hooks: ViewHooks | None,
    body: Callable[[dict[str, Any]], Any],
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_tenant(seen), input_serializer=_TenantIn, atomic=False, **spec_kwargs
    )

    dispatch(
        spec,
        params=body({"title": "t", "tenant": "evil"}),
        view_hooks=hooks,
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "seeded"),
    )

    assert seen == {"data": "server", "validated": "server", "tenant": "seeded"}
    assert _VALIDATED == ["server"]


# A field that reads more of a form body than its own key.


class _Inner(serializers.Serializer):
    tenant = serializers.CharField(required=False)


class _Nested(serializers.Serializer):
    v = _Inner(required=False)


class _Dict(serializers.Serializer):
    v = serializers.DictField(child=serializers.CharField(), required=False)


class _Many(serializers.Serializer):
    v = _Inner(many=True, required=False)


class _List(serializers.Serializer):
    v = serializers.ListField(child=serializers.CharField(), required=False)


# Each shape: the serializer, the server's value, the client's in a form body
# and in a JSON one.
_READS = {
    "nested": (_Nested, {"tenant": "server"}, {"v.tenant": "evil"}, {"v": {"tenant": "evil"}}),
    "dict": (_Dict, {"k": "server"}, {"v.k": "evil"}, {"v": {"k": "evil"}}),
    "many": (_Many, [{"tenant": "server"}], {"v[0]tenant": "evil"}, {"v": [{"tenant": "evil"}]}),
    "empty-list": (_List, [], {"v[0]": "evil"}, {"v": ["evil"]}),
    "list": (_List, ["server"], {"v[0]": "evil"}, {"v": ["evil"]}),
}
_READ = pytest.mark.parametrize(
    ("serializer", "server", "form", "body"), _READS.values(), ids=_READS.keys()
)


def _reads(seen: dict[str, Any]) -> Callable[..., None]:
    def service(
        *, data: Any, serializer: Any, v: Annotated[Any, NotClientInput] = "default"
    ) -> None:
        seen.update(v=v, data=data.get("v"), validated=serializer.validated_data.get("v"))

    return service


def _routes(server: Any) -> dict[str, tuple[dict[str, Any], ViewHooks | None]]:
    return {
        "hook-extra": ({}, ViewHooks(extra_kwargs={"v": server})),
        "spec-kwargs": ({"kwargs": lambda: {"v": server}}, None),
        "spec-input-data": ({"input_data": lambda: {"v": server}}, None),
    }


_ROUTE_IDS = pytest.mark.parametrize("route", ["hook-extra", "spec-kwargs", "spec-input-data"])


@pytest.mark.django_db
@_CORES
@_READ
@_ROUTE_IDS
@pytest.mark.parametrize("fmt", ["form", "mapping"])
def test_the_servers_value_is_the_one_put_however_the_field_reads_the_body_off_http(
    dispatch: Dispatch,
    serializer: Any,
    server: Any,
    form: dict[str, Any],
    body: dict[str, Any],
    route: str,
    fmt: str,
) -> None:
    seen: dict[str, Any] = {}
    spec_kwargs, hooks = _routes(server)[route]
    spec = ServiceSpec(
        service=_reads(seen), input_serializer=serializer, atomic=False, **spec_kwargs
    )

    dispatch(spec, params=_form(form) if fmt == "form" else body, view_hooks=hooks)

    assert seen == {"v": server, "data": server, "validated": server}


def _view_for(spec: ServiceSpec[Any, Any, Any], hook: Any) -> Any:
    attrs: dict[str, Any] = {"permission_classes": [], "spec": spec}
    if hook is not empty:
        attrs["get_service_kwargs"] = lambda self: {"v": hook}
    return type("_View", (ServiceCreateView,), attrs)


_HTTP_ROUTES = pytest.mark.parametrize(
    "route", ["get-service-kwargs", "spec-kwargs", "spec-input-data"]
)


def _http_spec(seen: dict[str, Any], serializer: Any, server: Any, route: str) -> Any:
    spec_kwargs: dict[str, Any] = {}
    if route == "spec-kwargs":
        spec_kwargs["kwargs"] = lambda: {"v": server}
    elif route == "spec-input-data":
        spec_kwargs["input_data"] = lambda: {"v": server}
    spec = ServiceSpec(
        service=_reads(seen), input_serializer=serializer, atomic=False, **spec_kwargs
    )
    return _view_for(spec, server if route == "get-service-kwargs" else empty)


@pytest.mark.django_db
@_READ
@_HTTP_ROUTES
@pytest.mark.parametrize("fmt", ["multipart", "json"])
def test_the_servers_value_is_the_one_put_however_the_field_reads_the_body_over_http(
    serializer: Any, server: Any, form: dict[str, Any], body: dict[str, Any], route: str, fmt: str
) -> None:
    seen: dict[str, Any] = {}
    view = _http_spec(seen, serializer, server, route)

    response = view.as_view()(_factory.post("/", form if fmt == "multipart" else body, format=fmt))

    assert response.status_code == 204, response.data
    assert seen == {"v": server, "data": server, "validated": server}


# A field whose own ``get_value`` reads another key of the body.


class _ReadsAnotherKey(serializers.CharField):
    def get_value(self, dictionary: Any) -> Any:
        return dictionary.get("v_raw", empty)


class _Raw(serializers.Serializer):
    v = _ReadsAnotherKey(required=False)


@pytest.mark.django_db
@_CORES
def test_a_field_reading_another_key_validates_the_value_put_off_http(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_reads(seen), input_serializer=_Raw, atomic=False)

    dispatch(spec, params={"v_raw": "evil"}, view_hooks=ViewHooks(extra_kwargs={"v": "server"}))

    assert seen == {"v": "server", "data": "server", "validated": "server"}


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_a_field_reading_another_key_validates_the_value_put_over_http(fmt: str) -> None:
    seen: dict[str, Any] = {}
    view = _http_spec(seen, _Raw, "server", "get-service-kwargs")

    response = view.as_view()(_factory.post("/", {"v_raw": "evil"}, format=fmt))

    assert response.status_code == 204, response.data
    assert seen == {"v": "server", "data": "server", "validated": "server"}


@pytest.mark.django_db(transaction=True)
async def test_a_field_reading_another_key_validates_the_value_put_on_a_live_async_view() -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_reads(seen), input_serializer=_Raw, atomic=False)
    view = ServiceCreateView()
    view.request = Request(
        _factory.post("/", {"v_raw": "evil"}, format="json"),
        parsers=[JSONParser(), MultiPartParser()],
    )
    view.request.user = AnonymousUser()
    view.kwargs = {}
    view.format_kwarg = None

    await adispatch_spec(
        spec,
        user=None,
        params=view.request.data,
        request=view.request,
        view=view,
        view_hooks=ViewHooks(extra_kwargs={"v": "server"}),
    )

    assert seen == {"v": "server", "data": "server", "validated": "server"}


# On a form body the server's value is read as the field reads a form, so a
# value written the way a form carries it keeps DRF's reading of one.


class _Optional(serializers.Serializer):
    v = serializers.CharField(required=False)


class _Nullable(serializers.Serializer):
    v = serializers.CharField(required=False, allow_null=True)


class _Choices(serializers.Serializer):
    v = serializers.MultipleChoiceField(choices=["a", "b"], required=False)


class _Json(serializers.Serializer):
    v = serializers.JSONField(required=False)


_FORM_READS = {
    # A form carries a list as repeated keys, so one value is a list of one.
    "scalar-for-a-list": (_List, "x", ["x"], ["x"]),
    "scalar-for-choices": (_Choices, "a", ["a"], ["a"]),
    # A form's empty string for an optional field is the field left out, or
    # ``None`` where the field allows null.
    "empty-string": (_Optional, "", "default", None),
    "empty-string-nullable": (_Nullable, "", None, None),
}


@pytest.mark.django_db
@_HTTP_ROUTES
@pytest.mark.parametrize(
    ("serializer", "server", "handed", "kept"), _FORM_READS.values(), ids=_FORM_READS.keys()
)
def test_a_server_value_on_a_form_body_is_read_as_a_form_reads_it(
    route: str, serializer: Any, server: Any, handed: Any, kept: Any
) -> None:
    seen: dict[str, Any] = {}
    view = _http_spec(seen, serializer, server, route)

    response = view.as_view()(_factory.post("/", {"v": "evil"}, format="multipart"))

    assert response.status_code == 204, response.data
    assert seen == {"v": handed, "data": kept, "validated": kept}


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["get-service-kwargs", "spec-kwargs"])
def test_a_json_fields_mapping_is_refused_on_a_form_body_and_taken_on_a_json_one(
    route: str,
) -> None:
    """The serializer reads a form's value for a ``JSONField`` as JSON text, so the
    answer depends on the client's content type."""
    seen: dict[str, Any] = {}
    view = _http_spec(seen, _Json, {"k": "server"}, route)

    form = view.as_view()(_factory.post("/", {"other": "x"}, format="multipart"))
    taken = view.as_view()(_factory.post("/", {"other": "x"}, format="json"))

    assert form.status_code == 400
    assert form.data == {"v": ["Value must be valid JSON."]}
    assert taken.status_code == 204, taken.data
    assert seen == {"v": {"k": "server"}, "data": {"k": "server"}, "validated": {"k": "server"}}


class _ReadsNothingOptional(_Optional):
    """Validates nothing of the body itself, so the server's value is validated by its run alone."""

    def to_internal_value(self, data: Any) -> Any:
        return {}


class _ReadsNothingList(_List):
    def to_internal_value(self, data: Any) -> Any:
        return {}


_JSON_TAKES = {
    # A JSON body carries a list as a list, so one value is refused as no list.
    "scalar-for-a-list": (
        _ReadsNothingList,
        "x",
        {"v": ['Expected a list of items but got type "str".']},
    ),
    # And an empty string is a value, refused where the field allows no blank.
    "empty-string": (_ReadsNothingOptional, "", {"v": ["This field may not be blank."]}),
}


@pytest.mark.django_db
@_HTTP_ROUTES
@pytest.mark.parametrize(
    ("serializer", "server", "refusal"), _JSON_TAKES.values(), ids=_JSON_TAKES.keys()
)
def test_a_server_value_on_a_json_body_is_taken_as_put(
    route: str, serializer: Any, server: Any, refusal: dict[str, Any]
) -> None:
    """Nothing reads it as a form, so the field validates the value as given."""
    seen: dict[str, Any] = {}
    view = _http_spec(seen, serializer, server, route)

    response = view.as_view()(_factory.post("/", {"v": ["evil"]}, format="json"))

    assert response.status_code == 400
    assert response.data == refusal
    assert seen == {}


# A hook or the provider naming a field by its wire name where its ``source``
# differs: the author named the parameter.


class _Sourced(serializers.Serializer):
    tenant = serializers.CharField(source="tenant_id", required=False)


def _with_default(seen: dict[str, Any]) -> Callable[..., None]:
    def service(*, data: Any, tenant: str = "D") -> None:
        seen.update(tenant=tenant, data=data.get("tenant_id"))

    return service


def _without_default(seen: dict[str, Any]) -> Callable[..., None]:
    def service(*, data: Any, tenant: str) -> None:
        seen.update(tenant=tenant, data=data.get("tenant_id"))

    return service


_DEFAULTED = pytest.mark.parametrize(
    "service_for", [_with_default, _without_default], ids=["with-default", "without-default"]
)
_AUTHOR_ROUTE = pytest.mark.parametrize(
    ("spec_kwargs", "hooks"),
    [
        ({}, ViewHooks(extra_kwargs={"tenant": "srv"})),
        ({"kwargs": lambda: {"tenant": "srv"}}, None),
    ],
    ids=["hook-extra", "spec-kwargs"],
)


@pytest.mark.django_db
@_CORES
@_AUTHOR_ROUTE
@_DEFAULTED
def test_an_author_value_by_wire_name_reaches_the_parameter_of_that_name_off_http(
    dispatch: Dispatch,
    spec_kwargs: dict[str, Any],
    hooks: ViewHooks | None,
    service_for: Callable[..., Callable[..., None]],
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=service_for(seen), input_serializer=_Sourced, atomic=False, **spec_kwargs
    )

    dispatch(spec, params={"tenant": "evil"}, view_hooks=hooks)

    assert seen == {"tenant": "srv", "data": "srv"}


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["get-service-kwargs", "spec-kwargs"])
@_DEFAULTED
def test_an_author_value_by_wire_name_reaches_the_parameter_of_that_name_over_http(
    route: str, service_for: Callable[..., Callable[..., None]]
) -> None:
    seen: dict[str, Any] = {}
    spec_kwargs: dict[str, Any] = (
        {"kwargs": lambda: {"tenant": "srv"}} if route == "spec-kwargs" else {}
    )
    spec = ServiceSpec(
        service=service_for(seen), input_serializer=_Sourced, atomic=False, **spec_kwargs
    )
    attrs: dict[str, Any] = {"permission_classes": [], "spec": spec}
    if route == "get-service-kwargs":
        attrs["get_service_kwargs"] = lambda self: {"tenant": "srv"}
    view = type("_View", (ServiceCreateView,), attrs)

    response = view.as_view()(_factory.post("/", {"tenant": "evil"}, format="json"))

    assert response.status_code == 204, response.data
    assert seen == {"tenant": "srv", "data": "srv"}


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize(
    ("spec_kwargs", "hooks"),
    [
        ({}, ViewHooks(extra_kwargs={"tenant_id": "srv"})),
        ({"kwargs": lambda: {"tenant_id": "srv"}}, None),
    ],
    ids=["hook-extra", "spec-kwargs"],
)
def test_an_author_value_by_source_reaches_the_parameter_of_that_name_alone(
    dispatch: Dispatch, spec_kwargs: dict[str, Any], hooks: ViewHooks | None
) -> None:
    """The author named ``tenant_id``, so the parameter named like the wire name keeps its default."""
    seen: dict[str, Any] = {}

    def service(*, data: Any, tenant_id: str = "D", tenant: str = "D") -> None:
        seen.update(tenant_id=tenant_id, tenant=tenant, data=data.get("tenant_id"))

    spec = ServiceSpec(service=service, input_serializer=_Sourced, atomic=False, **spec_kwargs)

    dispatch(spec, params={"tenant": "evil"}, view_hooks=hooks)

    assert seen == {"tenant_id": "srv", "tenant": "D", "data": "srv"}


@pytest.mark.django_db
@_CORES
def test_input_data_by_wire_name_leaves_the_parameter_of_that_name_its_default(
    dispatch: Dispatch,
) -> None:
    """``input_data`` is the body's, never an author's naming of a parameter, as on 0.57.0."""
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_with_default(seen),
        input_serializer=_Sourced,
        input_data=lambda: {"tenant": "srv"},
        atomic=False,
    )

    dispatch(spec, params={"tenant": "evil"})

    assert seen == {"tenant": "D", "data": "srv"}


# The cost of validating the server's value on its own, for a nested serializer.


@pytest.mark.django_db
def test_a_server_value_for_a_nested_serializer_runs_its_validation_twice() -> None:
    """Once in the serializer's own pass, once on its own: ``validate()`` and the children's."""
    calls: list[str] = []

    class _Counted(serializers.Serializer):
        tenant = serializers.CharField()

        def validate_tenant(self, value: str) -> str:
            calls.append("validate_tenant")
            return value

        def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
            calls.append("validate")
            return attrs

    class _Outer(serializers.Serializer):
        v = _Counted(required=False)

    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_reads(seen), input_serializer=_Outer, atomic=False)

    dispatch_spec(
        spec, user=None, params={}, view_hooks=ViewHooks(extra_kwargs={"v": {"tenant": "server"}})
    )

    assert calls == ["validate_tenant", "validate", "validate_tenant", "validate"]
    assert seen["v"] == {"tenant": "server"}
