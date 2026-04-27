"""Tests for the exceptions package."""

from __future__ import annotations

import pytest

from rest_framework_services.exceptions import ServiceError, ServiceValidationError


class TestServiceError:
    def test_default_message(self) -> None:
        err = ServiceError()
        assert err.message == ServiceError.default_message
        assert str(err) == ServiceError.default_message

    def test_custom_message(self) -> None:
        err = ServiceError("boom")
        assert err.message == "boom"
        assert str(err) == "boom"

    def test_is_exception(self) -> None:
        with pytest.raises(ServiceError, match="x"):
            raise ServiceError("x")


class TestServiceValidationError:
    def test_string_detail(self) -> None:
        err = ServiceValidationError("bad input")
        assert err.detail == "bad input"
        assert err.message == "bad input"
        assert str(err) == "bad input"

    def test_dict_detail(self) -> None:
        err = ServiceValidationError({"name": ["required"]})
        assert err.detail == {"name": ["required"]}
        assert err.message == ServiceValidationError.default_message

    def test_list_detail(self) -> None:
        err = ServiceValidationError(["a", "b"])
        assert err.detail == ["a", "b"]
        assert err.message == ServiceValidationError.default_message

    def test_inherits_from_service_error(self) -> None:
        with pytest.raises(ServiceError):
            raise ServiceValidationError("nope")

    def test_no_drf_imports_in_module(self) -> None:
        import re

        from rest_framework_services.exceptions import service_error, service_validation_error

        # Match `from rest_framework`/`import rest_framework` only when the
        # next character is NOT `_` (which would mean rest_framework_services).
        forbidden = re.compile(r"\b(?:from|import)\s+rest_framework(?!\w)")
        for module in (service_error, service_validation_error):
            source = module.__file__
            assert source is not None
            with open(source, encoding="utf-8") as fh:
                contents = fh.read()
            assert forbidden.search(contents) is None
