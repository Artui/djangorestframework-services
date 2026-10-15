"""A key the spec's typed ``kwargs`` provider is annotated to fill is never the client's.

Declining it with ``UNSET`` leaves the parameter its default, whatever field
the client's value came in through: one whose ``source`` is the key, a list,
dict, nested or nested ``many`` field a form body fills through prefixed keys
(``tags[0]``, ``meta.tenant``, ``d.k``, ``rows[0]tenant``), or a field named
like the key itself. The client's value stays the serializer's, so it is the
service's in ``data`` alone, as a ``NotClientInput`` key's is, and as 0.57.0
handed it. A view hook naming the key by the field's wire name is no way in
either.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.http import QueryDict
from django_service_specs.dispatch.dispatch import dispatch as kernel_dispatch
from django_service_specs.dispatch.grant import Grant
from django_service_specs.specs.kernel_spec import kernel_spec
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ArgumentBinding,
    DispatchResult,
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


def _form(values: dict[str, Any]) -> QueryDict:
    """A form body, as a multipart request's parser hands one on."""
    body = QueryDict(mutable=True)
    for name, value in values.items():
        body[name] = value
    return body


class _Inner(serializers.Serializer):
    tenant = serializers.CharField()


class _SourcedIn(serializers.Serializer):
    tenant = serializers.CharField(source="org", required=False)


class _ListIn(serializers.Serializer):
    tags = serializers.ListField(child=serializers.CharField(), required=False)


class _NestedIn(serializers.Serializer):
    meta = _Inner(required=False)


class _DictIn(serializers.Serializer):
    d = serializers.DictField(child=serializers.CharField(), required=False)


class _ManyIn(serializers.Serializer):
    rows = _Inner(many=True, required=False)


class _RequiredIn(serializers.Serializer):
    title = serializers.CharField()


# (serializer, the key the provider owns, a JSON body, a form body, ``data``)
_SHAPES: dict[str, tuple[type[serializers.Serializer], str, Any, Any, Any]] = {
    "source": (_SourcedIn, "org", {"tenant": "evil"}, {"tenant": "evil"}, {"org": "evil"}),
    "list": (_ListIn, "tags", {"tags": ["evil"]}, {"tags[0]": "evil"}, {"tags": ["evil"]}),
    "nested": (
        _NestedIn,
        "meta",
        {"meta": {"tenant": "evil"}},
        {"meta.tenant": "evil"},
        {"meta": {"tenant": "evil"}},
    ),
    "dict": (_DictIn, "d", {"d": {"k": "evil"}}, {"d.k": "evil"}, {"d": {"k": "evil"}}),
    "many": (
        _ManyIn,
        "rows",
        {"rows": [{"tenant": "evil"}]},
        {"rows[0]tenant": "evil"},
        {"rows": [{"tenant": "evil"}]},
    ),
    "required": (_RequiredIn, "title", {"title": "evil"}, {"title": "evil"}, {"title": "evil"}),
}
_SHAPE = pytest.mark.parametrize("shape", list(_SHAPES))


def _declining(key: str) -> Callable[[], Any]:
    """A provider annotated to fill ``key``, which declines it."""
    keys = TypedDict("_Keys", {key: Any})

    def provider() -> Any:
        return {key: UNSET}

    provider.__annotations__ = {"return": keys}
    return provider


def _services(seen: dict[str, Any]) -> dict[str, Callable[..., None]]:
    """A service per owned key, each with a parameter of that name and a default."""

    def org(*, data: Any, org: Any = "DEFAULT") -> None:
        seen.update(param=org, data=_plain(data))

    def tags(*, data: Any, tags: Any = "DEFAULT") -> None:
        seen.update(param=tags, data=_plain(data))

    def meta(*, data: Any, meta: Any = "DEFAULT") -> None:
        seen.update(param=meta, data=_plain(data))

    def d(*, data: Any, d: Any = "DEFAULT") -> None:
        seen.update(param=d, data=_plain(data))

    def rows(*, data: Any, rows: Any = "DEFAULT") -> None:
        seen.update(param=rows, data=_plain(data))

    def title(*, data: Any, title: Any = "DEFAULT") -> None:
        seen.update(param=title, data=_plain(data))

    return {"org": org, "tags": tags, "meta": meta, "d": d, "rows": rows, "title": title}


def _spec(shape: str, seen: dict[str, Any]) -> ServiceSpec[Any, Any, Any]:
    serializer, key = _SHAPES[shape][:2]
    return ServiceSpec(
        service=_services(seen)[key],
        input_serializer=serializer,
        kwargs=_declining(key),
        atomic=False,
    )


def _plain(value: Any) -> Any:
    """Validated data as plain dicts and lists, for comparing with a literal."""
    if isinstance(value, dict):
        return {name: _plain(item) for name, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


@pytest.mark.parametrize("fmt", ["json", "multipart"])
@_SHAPE
def test_a_declined_key_is_never_the_clients_over_http(shape: str, fmt: str) -> None:
    seen: dict[str, Any] = {}
    view = type(
        "_View", (ServiceCreateView,), {"permission_classes": [], "spec": _spec(shape, seen)}
    )
    json_body, form_body, data = _SHAPES[shape][2:]

    response = view.as_view()(
        _factory.post("/", json_body if fmt == "json" else form_body, format=fmt)
    )

    assert response.status_code == 204, response.data
    assert seen == {"param": "DEFAULT", "data": data}


@_CORES
@pytest.mark.parametrize("body", ["mapping", "form"])
@_SHAPE
def test_a_declined_key_is_never_the_clients_off_http(
    dispatch: Dispatch, body: str, shape: str
) -> None:
    seen: dict[str, Any] = {}
    json_body, form_body, data = _SHAPES[shape][2:]

    dispatch(_spec(shape, seen), params=json_body if body == "mapping" else _form(form_body))

    assert seen == {"param": "DEFAULT", "data": data}


@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_a_hook_naming_the_declined_key_by_wire_name_is_dropped_over_http(fmt: str) -> None:
    """``tenant`` is the field whose ``source`` is the owned ``org``: a hook's value for it is dropped."""
    seen: dict[str, Any] = {}
    view = type(
        "_View",
        (ServiceCreateView,),
        {
            "permission_classes": [],
            "spec": _spec("source", seen),
            "get_service_kwargs": lambda self: {"tenant": "hook"},
        },
    )

    response = view.as_view()(_factory.post("/", {"tenant": "evil"}, format=fmt))

    assert response.status_code == 204, response.data
    assert seen == {"param": "DEFAULT", "data": {"org": "evil"}}


@_CORES
@pytest.mark.parametrize("body", [dict, _form], ids=["mapping", "form"])
def test_a_hook_naming_the_declined_key_by_wire_name_is_dropped_off_http(
    dispatch: Dispatch, body: Callable[[dict[str, Any]], Any]
) -> None:
    seen: dict[str, Any] = {}

    dispatch(
        _spec("source", seen),
        params=body({"tenant": "evil"}),
        view_hooks=ViewHooks(extra_kwargs={"tenant": "hook"}),
    )

    assert seen == {"param": "DEFAULT", "data": {"org": "evil"}}


@_CORES
@pytest.mark.parametrize("body", ["mapping", "form"])
@_SHAPE
def test_a_declined_key_is_never_the_clients_under_a_spreading_binding(
    dispatch: Dispatch, body: str, shape: str
) -> None:
    """Spreading hands the caller's values to the service's own parameters, never an owned one."""
    seen: dict[str, Any] = {}
    json_body, form_body, data = _SHAPES[shape][2:]

    dispatch(
        _spec(shape, seen),
        params=json_body if body == "mapping" else _form(form_body),
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    )

    assert seen == {"param": "DEFAULT", "data": data}


@_CORES
def test_a_kernel_call_inside_the_service_keeps_its_own_values(dispatch: Dispatch) -> None:
    """The keys the outer call's provider owns are that call's: a spec the service
    dispatches through the kernel hands its own parameter of that name its value."""
    seen: dict[str, Any] = {}

    def inner(*, data: Any, title: Any = "DEFAULT") -> None:
        seen["inner"] = title

    inner_spec = kernel_spec(ServiceSpec(service=inner, input_serializer=_RequiredIn, atomic=False))

    def outer(*, data: Any) -> None:
        principal = AnonymousUser()
        kernel_dispatch(
            inner_spec,
            principal=principal,
            arguments={"title": "inner"},
            grant=Grant(inner_spec, principal),
        )

    spec = ServiceSpec(
        service=outer, input_serializer=_RequiredIn, kwargs=_declining("title"), atomic=False
    )

    dispatch(spec, params={"title": "outer"})

    assert seen == {"inner": "inner"}
