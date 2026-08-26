"""Tests for ``call_service``."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import exceptions as drf_exceptions
from rest_framework.test import APIRequestFactory

from rest_framework_services import call_service
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)


def _build_request(*, user: Any) -> Any:
    request = APIRequestFactory().get("/")
    request.user = user
    return request


def test_passes_request_and_user() -> None:
    captured: dict[str, Any] = {}

    def service(*, request: Any, user: Any) -> int:
        captured["request"] = request
        captured["user"] = user
        return 1

    request = _build_request(user="alice")
    result = call_service(service, request=request)

    assert result == 1
    assert captured["request"] is request
    assert captured["user"] == "alice"


def test_user_is_none_when_request_lacks_user_attr() -> None:
    """Bypassed-auth requests have no ``user`` attribute; helper passes ``None``."""

    class _BareRequest: ...

    captured: dict[str, Any] = {}

    def service(*, request: Any, user: Any) -> None:
        captured["user"] = user

    call_service(service, request=_BareRequest())  # type: ignore[arg-type]

    assert captured["user"] is None


def test_data_passed_when_present() -> None:
    captured: dict[str, Any] = {}

    def service(*, data: Any) -> None:
        captured["data"] = data

    call_service(service, request=_build_request(user="x"), data={"name": "Alice"})

    assert captured["data"] == {"name": "Alice"}


def test_data_omitted_when_unset() -> None:
    """Service that does not declare ``data`` is not passed it."""

    def service() -> str:
        return "ok"

    assert call_service(service, request=_build_request(user="x")) == "ok"


def test_instance_passed_when_present() -> None:
    captured: dict[str, Any] = {}

    def service(*, instance: Any) -> None:
        captured["instance"] = instance

    call_service(service, request=_build_request(user="x"), instance="the-instance")

    assert captured["instance"] == "the-instance"


def test_extras_merge_into_pool() -> None:
    captured: dict[str, Any] = {}

    def service(*, tenant_id: int, actor: str) -> None:
        captured["tenant_id"] = tenant_id
        captured["actor"] = actor

    call_service(
        service,
        request=_build_request(user="x"),
        tenant_id=42,
        actor="bob",
    )

    assert captured == {"tenant_id": 42, "actor": "bob"}


def test_extras_cannot_override_the_request_derived_user() -> None:
    """A ``user`` key spread in from client data never outranks ``request.user``."""
    captured: dict[str, Any] = {}

    def service(*, user: Any) -> None:
        captured["user"] = user

    validated_data = {"user": "mallory"}
    call_service(service, request=_build_request(user="alice"), **validated_data)

    assert captured["user"] == "alice"


def test_extras_still_carry_names_the_helper_does_not_seed() -> None:
    """Only seeds the helper derived are protected; anything else comes through."""
    captured: dict[str, Any] = {}

    def service(*, progress: Any) -> None:
        captured["progress"] = progress

    reporter = object()
    call_service(service, request=_build_request(user="alice"), progress=reporter)

    assert captured["progress"] is reporter


def test_signature_filter_drops_undeclared_keys() -> None:
    """Service that doesn't declare ``request`` doesn't receive it."""

    def service(*, tenant_id: int) -> int:
        return tenant_id

    result = call_service(service, request=_build_request(user="x"), tenant_id=7)

    assert result == 7


def test_async_service_bridged_via_async_to_sync() -> None:
    async def aservice(*, value: int) -> int:
        return value * 3

    result = call_service(aservice, request=_build_request(user="x"), value=4)
    assert result == 12


def test_returns_service_return_value() -> None:
    def service() -> dict[str, int]:
        return {"x": 1}

    assert call_service(service, request=_build_request(user="x")) == {"x": 1}


@pytest.mark.parametrize("missing", ["data", "instance"])
def test_unset_sentinel_skips_optional_keys(missing: str) -> None:
    """``data`` / ``instance`` only enter the pool when explicitly passed."""

    def service(**pool: Any) -> set[str]:
        return set(pool)

    keys = call_service(service, request=_build_request(user="x"))
    assert missing not in keys


def test_service_error_propagates_raw_by_default() -> None:
    def service() -> None:
        raise ServiceError("nope")

    with pytest.raises(ServiceError):
        call_service(service, request=_build_request(user="x"))


def test_map_errors_translates_validation_error() -> None:
    def service() -> None:
        raise ServiceValidationError({"name": ["required"]})

    with pytest.raises(drf_exceptions.ValidationError) as exc_info:
        call_service(service, request=_build_request(user="x"), map_errors=True)
    assert exc_info.value.detail == {"name": ["required"]}


def test_map_errors_translates_generic_service_error_to_422() -> None:
    def service() -> None:
        raise ServiceError("boom")

    with pytest.raises(drf_exceptions.APIException) as exc_info:
        call_service(service, request=_build_request(user="x"), map_errors=True)
    assert exc_info.value.status_code == 422


def test_map_errors_maps_from_async_service() -> None:
    async def aservice() -> None:
        raise ServiceValidationError({"x": ["bad"]})

    with pytest.raises(drf_exceptions.ValidationError):
        call_service(aservice, request=_build_request(user="x"), map_errors=True)


def test_non_service_error_is_never_mapped() -> None:
    def service() -> None:
        raise ValueError("unrelated")

    with pytest.raises(ValueError):
        call_service(service, request=_build_request(user="x"), map_errors=True)
