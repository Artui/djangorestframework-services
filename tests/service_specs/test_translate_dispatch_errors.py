"""Tests for ``translate_dispatch_errors``: the kernel's refusals leave as the dual-base classes.

Every published ladder in the family catches DRF's ``ValidationError`` and
``PermissionDenied`` by name, and none catches the kernel's bare classes, so
what dispatch raises has to reach them as both.
"""

from __future__ import annotations

import pytest
from django_service_specs.authorization.not_permitted import NotPermitted as KernelNotPermitted
from django_service_specs.authorization.principal_unavailable import (
    PrincipalUnavailable as KernelPrincipalUnavailable,
)
from django_service_specs.parameters.coded_message import CodedMessage
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from django_service_specs.services.service_error import ServiceError as KernelServiceError
from django_service_specs.types.dispatch_error import DispatchError
from rest_framework.exceptions import ErrorDetail, ValidationError

from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from rest_framework_services.service_specs.not_permitted import NotPermitted
from rest_framework_services.service_specs.principal_unavailable import PrincipalUnavailable
from rest_framework_services.service_specs.translate_dispatch_errors import (
    translate_dispatch_errors,
)


def _raised_through(error: BaseException) -> BaseException:
    """What leaves the translator when ``error`` is raised inside it."""
    with pytest.raises(BaseException) as excinfo, translate_dispatch_errors():
        raise error
    return excinfo.value


class TestTheKernelsRefusals:
    def test_invalid_arguments_leaves_as_the_dual_class_with_its_tree(self) -> None:
        error = KernelInvalidArguments(
            {"title": [CodedMessage("Required.", "required")], "rows": {2: {"n": ["Bad."]}}}
        )
        translated = _raised_through(error)
        assert type(translated) is InvalidArguments
        assert translated.__cause__ is error
        assert isinstance(translated, InvalidArguments)
        assert translated.detail == {"title": ["Required."], "rows": {2: {"n": ["Bad."]}}}
        assert isinstance(translated.detail["title"][0], ErrorDetail)
        assert translated.get_codes() == {"title": ["required"], "rows": {2: {"n": ["invalid"]}}}

    def test_not_permitted_leaves_as_the_dual_class_with_its_message(self) -> None:
        error = KernelNotPermitted("Only the owner may archive this.")
        translated = _raised_through(error)
        assert type(translated) is NotPermitted
        assert translated.__cause__ is error
        assert isinstance(translated, NotPermitted)
        assert translated.message == "Only the owner may archive this."
        assert translated.detail == "Only the owner may archive this."
        assert translated.get_codes() == "permission_denied"

    def test_principal_unavailable_leaves_as_the_dual_class_with_its_message(self) -> None:
        error = KernelPrincipalUnavailable()
        translated = _raised_through(error)
        assert type(translated) is PrincipalUnavailable
        assert translated.__cause__ is error
        assert isinstance(translated, PrincipalUnavailable)
        assert translated.message == KernelPrincipalUnavailable.default_message
        assert translated.detail == KernelPrincipalUnavailable.default_message

    def test_a_subclass_of_a_kernel_refusal_leaves_as_its_dual_class(self) -> None:
        class _OwnerOnly(KernelNotPermitted):
            default_message = "Only the owner may do this."

        translated = _raised_through(_OwnerOnly())
        assert type(translated) is NotPermitted
        assert translated.detail == "Only the owner may do this."

    async def test_it_translates_around_an_await(self) -> None:
        async def refuse() -> None:
            raise KernelNotPermitted()

        with pytest.raises(NotPermitted), translate_dispatch_errors():
            await refuse()


class TestWhatItLeavesAlone:
    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(InvalidArguments({"title": ["No."]}), id="dual-invalid-arguments"),
            pytest.param(NotPermitted(), id="dual-not-permitted"),
            pytest.param(PrincipalUnavailable(), id="dual-principal-unavailable"),
        ],
    )
    def test_a_refusal_drf_already_answers_passes_through(self, error: DispatchError) -> None:
        # A validator raising the dual class directly is already caught by both
        # ladders; rebuilding it would only lose its traceback.
        left = _raised_through(error)
        assert left is error
        assert left.__cause__ is None

    def test_a_dispatch_error_with_no_drf_twin_passes_through(self) -> None:
        # Not invented here: a refusal with no DRF counterpart in this package
        # keeps the kernel's class and its answer is the transport's to choose.
        class _Unmapped(DispatchError): ...

        error = _Unmapped()
        assert _raised_through(error) is error

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(ValueError("a bug"), id="value-error"),
            pytest.param(KernelServiceError("refused"), id="service-error"),
            pytest.param(ValidationError({"x": ["No."]}), id="drf-validation-error"),
        ],
    )
    def test_anything_that_is_not_a_dispatch_error_passes_through(self, error: Exception) -> None:
        assert _raised_through(error) is error

    def test_no_error_is_no_change(self) -> None:
        with translate_dispatch_errors():
            result = 1
        assert result == 1
