"""Tests for ``DRFServiceValidationError``: a service's DRF refusal as the kernel's service validation error."""

from __future__ import annotations

from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from django_service_specs.services.service_validation_error import (
    ServiceValidationError as KernelServiceValidationError,
)
from django_service_specs.types.dispatch_error import DispatchError
from rest_framework.exceptions import ErrorDetail, ValidationError

from rest_framework_services import ServiceValidationError
from rest_framework_services.service_specs.drf_service_validation_error import (
    DRFServiceValidationError,
)
from tests.service_specs.served_status import served_status


class TestItIsBothClasses:
    def test_it_is_the_kernels_service_validation_error_and_drfs(self) -> None:
        assert issubclass(DRFServiceValidationError, KernelServiceValidationError)
        assert issubclass(DRFServiceValidationError, ValidationError)

    def test_it_is_not_the_root_s_class(self) -> None:
        # The root re-exports the kernel's own class, which this is a twin of.
        assert ServiceValidationError is KernelServiceValidationError
        assert DRFServiceValidationError is not ServiceValidationError

    def test_it_is_no_refusal_of_the_arguments(self) -> None:
        # A business rule about well-shaped input, not the arguments' shape.
        assert not issubclass(DRFServiceValidationError, KernelInvalidArguments)
        assert not issubclass(DRFServiceValidationError, DispatchError)


class TestBothInitsRan:
    def test_a_string_detail_is_the_message(self) -> None:
        exc = DRFServiceValidationError("Archived.")
        assert exc.message == "Archived."
        assert exc.detail == ["Archived."]

    def test_a_translated_message_is_the_kernel_s_default(self) -> None:
        """DRF makes a list of a single message, which the kernel reads as no message."""
        exc = DRFServiceValidationError(ValidationError("Archived.").detail)
        assert exc.message == KernelServiceValidationError.default_message
        assert exc.detail == ["Archived."]

    def test_every_message_is_an_error_detail_keeping_its_code(self) -> None:
        exc = DRFServiceValidationError(
            ValidationError({"title": ["Too long."]}, code="too_long").detail
        )
        assert isinstance(exc.detail, dict)
        assert isinstance(exc.detail["title"][0], ErrorDetail)
        assert exc.get_codes() == {"title": ["too_long"]}

    def test_a_code_given_is_kept(self) -> None:
        assert DRFServiceValidationError({"title": ["No."]}, code="locked").get_codes() == {
            "title": ["locked"]
        }


def test_drf_answers_it_400() -> None:
    assert served_status(DRFServiceValidationError("Archived.")) == 400
