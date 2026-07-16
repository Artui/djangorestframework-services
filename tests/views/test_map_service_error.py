"""Tests for views/mutation/map_service_error.py."""

from __future__ import annotations

from rest_framework import exceptions as drf_exceptions
from rest_framework import status as drf_status

from rest_framework_services.exceptions import ServiceError, ServiceValidationError
from rest_framework_services.views.mutation.map_service_error import (
    _ServiceAPIException,
    map_service_error,
)


class TestMapServiceError:
    def test_validation_error_maps_to_drf_validation(self) -> None:
        exc = map_service_error(ServiceValidationError({"name": ["required"]}))
        assert isinstance(exc, drf_exceptions.ValidationError)
        assert exc.detail == {"name": ["required"]}

    def test_generic_service_error_maps_to_422(self) -> None:
        exc = map_service_error(ServiceError("nope"))
        assert isinstance(exc, _ServiceAPIException)
        assert exc.status_code == drf_status.HTTP_422_UNPROCESSABLE_ENTITY


class TestServiceAPIException:
    def test_default_detail(self) -> None:
        exc = _ServiceAPIException()
        assert exc.detail == "Service error."
        assert exc.status_code == 422

    def test_custom_detail(self) -> None:
        exc = _ServiceAPIException("custom")
        assert str(exc.detail) == "custom"
