"""Tests for the dual-base ``NotPermitted``: the kernel's denial that DRF answers 403.

The kernel's ``__init__`` reaches DRF's through ``super()`` in this order of
bases, so ``detail`` is DRF's either way; what a reversed order loses is the
kernel's ``message``, which never gets set. Both are asserted, with the codes
and the ``ErrorDetail`` type rather than a response body, which a broken class
answers identically.
"""

from __future__ import annotations

import pytest
from django_service_specs.authorization.not_permitted import NotPermitted as KernelNotPermitted
from django_service_specs.services.service_error import ServiceError as KernelServiceError
from django_service_specs.types.dispatch_error import DispatchError
from rest_framework.exceptions import ErrorDetail, PermissionDenied

from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.service_specs.not_permitted import NotPermitted
from tests.service_specs.served_status import served_status


class TestItIsBothClasses:
    def test_it_is_the_kernels_denial_and_drfs(self) -> None:
        assert issubclass(NotPermitted, KernelNotPermitted)
        assert issubclass(NotPermitted, DispatchError)
        assert issubclass(NotPermitted, PermissionDenied)

    @pytest.mark.parametrize("service_error", [KernelServiceError, ServiceError])
    def test_it_is_never_a_service_error(self, service_error: type[Exception]) -> None:
        # A model reading a denial as a service refusal routes around it and
        # keeps trying, where a denial should end the attempt.
        assert not issubclass(NotPermitted, service_error)


class TestBothInitsRan:
    def test_the_default_is_the_kernels_message(self) -> None:
        exc = NotPermitted()
        assert exc.message == KernelNotPermitted.default_message
        assert exc.detail == KernelNotPermitted.default_message

    def test_a_message_is_both_the_kernels_message_and_drfs_detail(self) -> None:
        exc = NotPermitted("Only the owner may archive this.")
        assert exc.message == "Only the owner may archive this."
        assert isinstance(exc.detail, ErrorDetail)
        assert exc.detail == "Only the owner may archive this."

    def test_get_codes_is_drfs_denial_code(self) -> None:
        assert NotPermitted().get_codes() == "permission_denied"

    def test_a_code_given_is_kept(self) -> None:
        assert NotPermitted("No.", code="not_owner").get_codes() == "not_owner"


class TestDrfAnswersIt:
    def test_drf_answers_it_403(self) -> None:
        assert served_status(NotPermitted()) == 403

    def test_the_kernels_class_alone_is_a_500(self) -> None:
        assert served_status(KernelNotPermitted()) == 500
