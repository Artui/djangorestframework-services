"""A view hook naming one field by both its wire name and its ``source``.

The author named two keys, so each reaches the parameter of its name, as on
0.57.0: neither is dropped for the other. The wire name's value is the field's,
validated and handed on in ``data``, and the ``source``'s goes to the parameter
of that name beside it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
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


class _SourcedIn(serializers.Serializer):
    title = serializers.CharField(required=False)
    tenant = serializers.CharField(source="org", required=False)


def _service(seen: dict[str, Any], *, defaults: bool) -> Callable[..., None]:
    if defaults:

        def with_defaults(*, data: Any, tenant: str = "DEFAULT", org: str = "DEFAULT") -> None:
            seen.update(tenant=tenant, org=org, data=dict(data))

        return with_defaults

    def required(*, data: Any, tenant: str, org: str) -> None:
        seen.update(tenant=tenant, org=org, data=dict(data))

    return required


_DEFAULTS = pytest.mark.parametrize("defaults", [True, False], ids=["defaults", "required"])
_BOTH = {"tenant": "A", "org": "B"}
_HANDED = {"tenant": "A", "org": "B", "data": {"org": "A"}}


@_DEFAULTS
@pytest.mark.parametrize("fmt", ["json", "multipart"])
def test_each_key_reaches_its_own_parameter_over_http(defaults: bool, fmt: str) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_service(seen, defaults=defaults), input_serializer=_SourcedIn, atomic=False
    )
    view = type(
        "_View",
        (ServiceCreateView,),
        {"permission_classes": [], "spec": spec, "get_service_kwargs": lambda self: dict(_BOTH)},
    )

    response = view.as_view()(_factory.post("/", {"tenant": "evil", "org": "evil2"}, format=fmt))

    assert response.status_code == 204, response.data
    assert seen == _HANDED


@_CORES
@_DEFAULTS
def test_each_key_reaches_its_own_parameter_off_http(dispatch: Dispatch, defaults: bool) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_service(seen, defaults=defaults), input_serializer=_SourcedIn, atomic=False
    )

    dispatch(spec, params={"tenant": "evil"}, view_hooks=ViewHooks(extra_kwargs=dict(_BOTH)))

    assert seen == _HANDED


@_CORES
def test_a_declined_wire_name_leaves_the_field_to_the_source(dispatch: Dispatch) -> None:
    """The wire name declined with ``UNSET`` claims nothing, so the ``source``'s value is the field's."""
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_service(seen, defaults=True), input_serializer=_SourcedIn, atomic=False
    )

    dispatch(
        spec,
        params={"tenant": "evil"},
        view_hooks=ViewHooks(extra_kwargs={"tenant": UNSET, "org": "B"}),
    )

    assert seen == {"tenant": "DEFAULT", "org": "B", "data": {"org": "B"}}
