"""The author's values for a service outrank every key its Validator hands back.

A view's ``get_*_kwargs`` hooks and the spec's ``kwargs`` provider are the
server's, and a service has always received their value for a keyword over
anything the client's input became: a key a serializer's ``validate()`` derives
from the body, or one a ``source="*"`` nested serializer flattens into
``validated_data``. The client's value stays where it was validated, in
``data``. The serializer the Validator binds is not such a key: it stays the
bound one, as it always did. Each test runs over HTTP or through both cores,
against the public signatures.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.test import APIRequestFactory
from typing_extensions import TypedDict

from rest_framework_services import (
    DispatchResult,
    ServiceCreateView,
    ServiceSpec,
    ViewHooks,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.service_specs.utils import AUTHOR_VALUES_SEED

_factory = APIRequestFactory()


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


class _DerivingIn(serializers.Serializer):
    """A ``validate()`` that turns a client's field into a key no field declares."""

    title = serializers.CharField()
    claimed = serializers.CharField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "claimed" in attrs:
            attrs["tenant_id"] = attrs.pop("claimed")
        return attrs


class _TenantPart(serializers.Serializer):
    tenant_id = serializers.CharField(required=False)


class _FlatteningIn(serializers.Serializer):
    """A ``source="*"`` nested serializer, whose fields land at the top of ``validated_data``."""

    title = serializers.CharField()
    meta = _TenantPart(source="*", required=False)


class _Tenant(TypedDict):
    tenant_id: str


def _untyped_provider() -> dict[str, Any]:
    return {"tenant_id": "server"}


def _typed_provider() -> _Tenant:
    return {"tenant_id": "server"}


_BODY = {"title": "t", "claimed": "evil", "meta": {"tenant_id": "evil"}}


def _recording(seen: dict[str, Any]) -> Callable[..., None]:
    def service(*, data: Any, tenant_id: str = "none") -> None:
        seen["tenant_id"] = tenant_id
        seen["data"] = dict(data)

    return service


@pytest.mark.django_db
@pytest.mark.parametrize("serializer", [_DerivingIn, _FlatteningIn], ids=["validate", "star"])
def test_a_view_hooks_value_outranks_the_validators_over_http(serializer: Any) -> None:
    seen: dict[str, Any] = {}

    class _View(ServiceCreateView):
        spec = ServiceSpec(service=_recording(seen), input_serializer=serializer, atomic=False)

        def get_service_kwargs(self) -> dict[str, Any]:
            return {"tenant_id": "server"}

    response = _View.as_view()(_factory.post("/", _BODY, format="json"))

    assert response.status_code in (201, 204)
    assert seen == {"tenant_id": "server", "data": {"title": "t", "tenant_id": "evil"}}


@pytest.mark.django_db
@pytest.mark.parametrize("serializer", [_DerivingIn, _FlatteningIn], ids=["validate", "star"])
@pytest.mark.parametrize("provider", [_untyped_provider, _typed_provider], ids=["untyped", "typed"])
def test_the_specs_provider_outranks_the_validators_over_http(
    serializer: Any, provider: Callable[..., Any]
) -> None:
    seen: dict[str, Any] = {}

    class _View(ServiceCreateView):
        spec = ServiceSpec(
            service=_recording(seen), input_serializer=serializer, kwargs=provider, atomic=False
        )

    response = _View.as_view()(_factory.post("/", _BODY, format="json"))

    assert response.status_code in (201, 204)
    assert seen == {"tenant_id": "server", "data": {"title": "t", "tenant_id": "evil"}}


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize("serializer", [_DerivingIn, _FlatteningIn], ids=["validate", "star"])
def test_a_hooks_value_outranks_the_validators_off_http(
    dispatch: Dispatch, serializer: Any
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_recording(seen), input_serializer=serializer, atomic=False)

    dispatch(spec, params=_BODY, view_hooks=ViewHooks(extra_kwargs={"tenant_id": "server"}))

    assert seen == {"tenant_id": "server", "data": {"title": "t", "tenant_id": "evil"}}


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize("serializer", [_DerivingIn, _FlatteningIn], ids=["validate", "star"])
@pytest.mark.parametrize("provider", [_untyped_provider, _typed_provider], ids=["untyped", "typed"])
def test_the_specs_provider_outranks_the_validators_off_http(
    dispatch: Dispatch, serializer: Any, provider: Callable[..., Any]
) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_recording(seen), input_serializer=serializer, kwargs=provider, atomic=False
    )

    dispatch(spec, params=_BODY)

    assert seen == {"tenant_id": "server", "data": {"title": "t", "tenant_id": "evil"}}


@pytest.mark.django_db
@_CORES
def test_the_specs_provider_still_outranks_the_hooks(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(
        service=_recording(seen),
        input_serializer=_DerivingIn,
        kwargs=_untyped_provider,
        atomic=False,
    )

    dispatch(spec, params=_BODY, view_hooks=ViewHooks(extra_kwargs={"tenant_id": "hook"}))

    assert seen["tenant_id"] == "server"


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


@pytest.mark.django_db
@_CORES
def test_the_bound_serializer_outranks_an_authors_value_of_its_name(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}

    def service(*, data: Any, serializer: Any) -> None:
        seen["serializer"] = serializer

    spec = ServiceSpec(
        service=service,
        input_serializer=_TitleIn,
        kwargs=lambda: {"serializer": "provider"},
        atomic=False,
    )

    dispatch(spec, params={"title": "t"}, view_hooks=ViewHooks(extra_kwargs={"serializer": "hook"}))

    assert isinstance(seen["serializer"], _TitleIn)


@pytest.mark.django_db
@_CORES
def test_an_authors_serializer_value_reaches_a_service_no_validator_binds_one_for(
    dispatch: Dispatch,
) -> None:
    seen: dict[str, Any] = {}

    def service(*, serializer: Any = "default", tenant_id: str = "none") -> None:
        seen["serializer"] = serializer
        seen["tenant_id"] = tenant_id

    spec = ServiceSpec(service=service, atomic=False)
    hooks = ViewHooks(extra_kwargs={"serializer": "hook", "tenant_id": "server"})

    dispatch(spec, params={}, view_hooks=hooks)

    assert seen == {"serializer": "hook", "tenant_id": "server"}


@pytest.mark.django_db
@_CORES
def test_a_call_with_no_author_value_registers_no_seat_for_them(dispatch: Dispatch) -> None:
    # A seed is in every pool, so an open service would be handed an empty one.
    seen: dict[str, Any] = {}

    def service(*, data: Any, **rest: Any) -> None:
        seen["rest"] = set(rest)

    dispatch(
        ServiceSpec(service=service, input_serializer=_TitleIn, atomic=False), params={"title": "t"}
    )

    assert AUTHOR_VALUES_SEED not in seen["rest"]
