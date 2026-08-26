"""Tests for ``acall_service``."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import exceptions as drf_exceptions
from rest_framework.test import APIRequestFactory

from rest_framework_services import acall_service
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)


def _build_request(*, user: Any) -> Any:
    request = APIRequestFactory().get("/")
    request.user = user
    return request


@pytest.mark.asyncio
async def test_async_service_awaited() -> None:
    async def aservice(*, request: Any, user: Any, value: int) -> int:
        assert request is not None
        assert user == "alice"
        return value * 2

    result = await acall_service(aservice, request=_build_request(user="alice"), value=5)
    assert result == 10


@pytest.mark.asyncio
async def test_sync_service_called_inline() -> None:
    def service(*, value: int) -> int:
        return value + 1

    result = await acall_service(service, request=_build_request(user="x"), value=4)
    assert result == 5


@pytest.mark.asyncio
async def test_data_and_instance_pass_through() -> None:
    captured: dict[str, Any] = {}

    async def aservice(*, data: Any, instance: Any) -> None:
        captured["data"] = data
        captured["instance"] = instance

    await acall_service(
        aservice,
        request=_build_request(user="x"),
        data={"k": 1},
        instance="inst",
    )

    assert captured == {"data": {"k": 1}, "instance": "inst"}


@pytest.mark.asyncio
async def test_signature_filter_drops_undeclared_keys() -> None:
    async def aservice(*, tenant_id: int) -> int:
        return tenant_id

    result = await acall_service(
        aservice, request=_build_request(user="x"), tenant_id=7, ignored="zz"
    )
    assert result == 7


@pytest.mark.asyncio
async def test_service_error_propagates_raw_by_default() -> None:
    async def aservice() -> None:
        raise ServiceError("nope")

    with pytest.raises(ServiceError):
        await acall_service(aservice, request=_build_request(user="x"))


@pytest.mark.asyncio
async def test_map_errors_translates_validation_error() -> None:
    async def aservice() -> None:
        raise ServiceValidationError({"name": ["required"]})

    with pytest.raises(drf_exceptions.ValidationError) as exc_info:
        await acall_service(aservice, request=_build_request(user="x"), map_errors=True)
    assert exc_info.value.detail == {"name": ["required"]}


@pytest.mark.asyncio
async def test_map_errors_maps_generic_error_from_sync_service() -> None:
    def service() -> None:
        raise ServiceError("boom")

    with pytest.raises(drf_exceptions.APIException) as exc_info:
        await acall_service(service, request=_build_request(user="x"), map_errors=True)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_extras_cannot_override_the_request_derived_user() -> None:
    """Sync twin's guard, async side: ``request.user`` outranks an ``extras`` key."""
    captured: dict[str, Any] = {}

    async def aservice(*, user: Any) -> None:
        captured["user"] = user

    validated_data = {"user": "mallory"}
    await acall_service(aservice, request=_build_request(user="alice"), **validated_data)

    assert captured["user"] == "alice"


@pytest.mark.asyncio
async def test_extras_still_carry_names_the_helper_does_not_seed() -> None:
    captured: dict[str, Any] = {}

    async def aservice(*, progress: Any) -> None:
        captured["progress"] = progress

    reporter = object()
    await acall_service(aservice, request=_build_request(user="alice"), progress=reporter)

    assert captured["progress"] is reporter
