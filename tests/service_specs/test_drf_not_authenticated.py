"""Tests for ``DRFNotAuthenticated``: DRF's ``NotAuthenticated`` as the kernel's denial, answered 401."""

from __future__ import annotations

from typing import Any

from django_service_specs.authorization.not_permitted import NotPermitted as KernelNotPermitted
from django_service_specs.services.service_error import ServiceError as KernelServiceError
from rest_framework.authentication import BasicAuthentication
from rest_framework.exceptions import ErrorDetail, NotAuthenticated, PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from rest_framework_services.service_specs.drf_not_authenticated import DRFNotAuthenticated


class TestItIsBothClasses:
    def test_it_is_the_kernels_denial_and_drfs_not_authenticated(self) -> None:
        assert issubclass(DRFNotAuthenticated, KernelNotPermitted)
        assert issubclass(DRFNotAuthenticated, NotAuthenticated)

    def test_it_is_neither_a_service_error_nor_drfs_permission_denied(self) -> None:
        # A permission denied is answered 403, where nobody signed in is 401.
        assert not issubclass(DRFNotAuthenticated, KernelServiceError)
        assert not issubclass(DRFNotAuthenticated, PermissionDenied)


class TestBothInitsRan:
    def test_the_default_is_drf_s_message(self) -> None:
        exc = DRFNotAuthenticated()
        assert exc.message == "Authentication credentials were not provided."
        assert exc.detail == exc.message

    def test_a_message_given_is_the_kernel_s(self) -> None:
        exc = DRFNotAuthenticated("Sign in to archive posts.")
        assert exc.message == "Sign in to archive posts."
        assert isinstance(exc.detail, ErrorDetail)
        assert exc.detail == "Sign in to archive posts."

    def test_get_codes_is_drf_s_code(self) -> None:
        assert DRFNotAuthenticated().get_codes() == "not_authenticated"

    def test_a_code_given_is_kept(self) -> None:
        assert DRFNotAuthenticated("No.", code="session_expired").get_codes() == "session_expired"


def test_drf_answers_it_401_with_the_authenticator_s_challenge() -> None:
    class _Raising(APIView):
        authentication_classes: Any = [BasicAuthentication]
        permission_classes: Any = []

        def get(self, request: Request) -> Response:
            raise DRFNotAuthenticated()

    response = _Raising.as_view()(APIRequestFactory().get("/"))

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == 'Basic realm="api"'
