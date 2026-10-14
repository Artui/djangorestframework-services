"""A validated key named like a seed stays in ``data`` and never replaces the seed.

DRF's canonical ``HiddenField(default=CurrentUserDefault())`` named ``user``
validates the principal into ``validated_data["user"]``; a ``validate()`` may add
a key named like any seed. The service has always received the serializer's
``validated_data`` whole as ``data``, and the seed under its own name. The
kernel refuses a validated value named like a seed, so the Validator hands such
a key on in ``data`` alone. Each test runs over HTTP or through both cores, and
each is written against the public signatures.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from rest_framework import serializers
from rest_framework.test import APIRequestFactory, force_authenticate

from rest_framework_services import (
    DispatchResult,
    ServiceCreateView,
    ServiceSpec,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.types.pool_seeds import PoolSeeds

_factory = APIRequestFactory()


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


class _CurrentUserIn(serializers.Serializer):
    title = serializers.CharField()
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())


class _TenantIn(serializers.Serializer):
    title = serializers.CharField()
    tenant = serializers.HiddenField(default="from-the-field")


class _AddsResultIn(serializers.Serializer):
    title = serializers.CharField()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return {**attrs, "result": "added", "progress": "added"}


def _record(seen: dict[str, Any]) -> Callable[..., None]:
    def service(*, data: Any, user: Any = None, tenant: Any = None, **rest: Any) -> None:
        seen["data"] = dict(data)
        seen["user"] = user
        seen["tenant"] = tenant
        seen["progress"] = rest.get("progress")

    return service


@pytest.mark.django_db
def test_a_current_user_hidden_field_named_user_validates_over_http() -> None:
    seen: dict[str, Any] = {}
    alice = User.objects.create(username="alice")

    class _View(ServiceCreateView):
        spec = ServiceSpec(service=_record(seen), input_serializer=_CurrentUserIn, atomic=False)

    request = _factory.post("/", {"title": "t"}, format="json")
    force_authenticate(request, user=alice)
    response = _View.as_view()(request)

    assert response.status_code in (201, 204)
    assert seen["data"] == {"title": "t", "user": alice}
    assert seen["user"] == alice


@pytest.mark.django_db
@_CORES
def test_a_current_user_hidden_field_named_user_validates_off_http(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}
    alice = User.objects.create(username="alice")
    spec = ServiceSpec(service=_record(seen), input_serializer=_CurrentUserIn, atomic=False)

    dispatch(spec, user=alice, params={"title": "t"})

    assert seen["data"] == {"title": "t", "user": alice}
    assert seen["user"] is alice


@pytest.mark.django_db
@_CORES
def test_a_key_named_like_a_registered_seed_stays_in_data(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_record(seen), input_serializer=_TenantIn, atomic=False)

    dispatch(
        spec,
        user=None,
        params={"title": "t"},
        pool_seeds=PoolSeeds(seeds=(("tenant", lambda: "the-seed"),)),
    )

    assert seen["data"] == {"title": "t", "tenant": "from-the-field"}
    assert seen["tenant"] == "the-seed"


@pytest.mark.django_db
@_CORES
def test_a_key_a_validate_adds_named_like_a_seed_stays_in_data(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}
    spec = ServiceSpec(service=_record(seen), input_serializer=_AddsResultIn, atomic=False)

    dispatch(spec, user=None, params={"title": "t"})

    assert seen["data"] == {"title": "t", "result": "added", "progress": "added"}
    # The seed, never the validated value: the transport's reporter, or the null one.
    assert seen["progress"] != "added"


class _AddsSerializerIn(serializers.Serializer):
    title = serializers.CharField()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return {**attrs, "serializer": "added"}


@pytest.mark.django_db
@_CORES
def test_a_key_a_validate_adds_named_like_the_bound_serializer_stays_in_data(
    dispatch: Dispatch,
) -> None:
    seen: dict[str, Any] = {}

    def service(*, data: Any, serializer: Any) -> None:
        seen["data"] = dict(data)
        seen["serializer"] = serializer

    spec = ServiceSpec(service=service, input_serializer=_AddsSerializerIn, atomic=False)

    dispatch(spec, user=None, params={"title": "t"})

    assert seen["data"] == {"title": "t", "serializer": "added"}
    assert isinstance(seen["serializer"], _AddsSerializerIn)


@pytest.mark.django_db
@_CORES
def test_each_rows_seed_named_key_stays_in_its_row(dispatch: Dispatch) -> None:
    seen: dict[str, Any] = {}
    alice = User.objects.create(username="alice")

    def bulk(*, data: Any, user: Any) -> None:
        seen["data"] = [dict(row) for row in data]
        seen["user"] = user

    spec = ServiceSpec(service=bulk, input_serializer=_CurrentUserIn, many=True, atomic=False)

    dispatch(spec, user=alice, params=[{"title": "a"}, {"title": "b"}])

    assert seen["data"] == [{"title": "a", "user": alice}, {"title": "b", "user": alice}]
    assert seen["user"] is alice
