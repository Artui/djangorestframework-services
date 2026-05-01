"""Tests for views/mutation/utils.py — leaf helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework import exceptions as drf_exceptions
from rest_framework import serializers
from rest_framework import status as drf_status
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services.exceptions import ServiceError, ServiceValidationError
from rest_framework_services.views.mutation.utils import (
    _ServiceAPIException,
    dispatch_service,
    map_service_error,
    validate_input,
)


@dataclass
class _Sample:
    name: str


factory = APIRequestFactory()


def _drf_request(method: str, data: Any = None) -> Request:
    """Wrap an APIRequestFactory call in a DRF :class:`Request`."""
    raw = getattr(factory, method)("/", data, format="json")
    return Request(raw, parsers=[JSONParser()])


class TestValidateInput:
    def test_returns_none_when_input_is_none(self) -> None:
        assert validate_input(_drf_request("post", {"name": "x"}), None) is None

    def test_dataclass_returns_instance(self) -> None:
        result = validate_input(_drf_request("post", {"name": "Ada"}), _Sample)
        assert isinstance(result, _Sample)
        assert result.name == "Ada"

    def test_raises_validation_error_when_invalid(self) -> None:
        with pytest.raises(drf_exceptions.ValidationError):
            validate_input(_drf_request("post", {}), _Sample)

    def test_partial_skips_required(self) -> None:
        @dataclass
        class _Partial:
            name: str = ""

        result = validate_input(_drf_request("patch", {}), _Partial, partial=True)
        assert isinstance(result, _Partial)

    def test_dataclass_serializer_subclass_returns_dataclass_instance(self) -> None:
        class _SampleSerializer(DataclassSerializer):
            class Meta:
                dataclass = _Sample

        result = validate_input(_drf_request("post", {"name": "Ada"}), _SampleSerializer)
        assert isinstance(result, _Sample)
        assert result.name == "Ada"

    def test_plain_serializer_subclass_returns_dict(self) -> None:
        class _PlainSerializer(serializers.Serializer):
            name = serializers.CharField()

        result = validate_input(_drf_request("post", {"name": "Ada"}), _PlainSerializer)
        assert result == {"name": "Ada"}

    def test_rejects_unsupported_input_spec(self) -> None:
        with pytest.raises(TypeError, match="must be a dataclass type or a Serializer"):
            validate_input(_drf_request("post", {}), object)  # type: ignore[arg-type]

    def test_extra_data_merged_on_top_of_request_body(self) -> None:
        @dataclass
        class _WithParent:
            name: str
            parent_id: int

        result = validate_input(
            _drf_request("post", {"name": "Ada"}),
            _WithParent,
            extra_data={"parent_id": 7},
        )
        assert isinstance(result, _WithParent)
        assert result.name == "Ada"
        assert result.parent_id == 7

    def test_extra_data_wins_over_request_body_on_conflict(self) -> None:
        @dataclass
        class _WithParent:
            name: str
            parent_id: int

        result = validate_input(
            _drf_request("post", {"name": "Ada", "parent_id": 1}),
            _WithParent,
            extra_data={"parent_id": 99},
        )
        assert result.parent_id == 99

    def test_extra_data_works_with_plain_serializer(self) -> None:
        class _PlainSerializer(serializers.Serializer):
            name = serializers.CharField()
            parent_id = serializers.IntegerField()

        result = validate_input(
            _drf_request("post", {"name": "Ada"}),
            _PlainSerializer,
            extra_data={"parent_id": 7},
        )
        assert result == {"name": "Ada", "parent_id": 7}


class TestDispatchService:
    @pytest.mark.django_db
    def test_sync_service_atomic(self) -> None:
        def fn(*, x: int) -> int:
            return x * 2

        assert dispatch_service(fn, {"x": 3}, atomic=True) == 6

    @pytest.mark.django_db
    def test_async_service_bridged(self) -> None:
        async def fn(*, x: int) -> int:
            return x + 1

        assert dispatch_service(fn, {"x": 4}, atomic=False) == 5


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
