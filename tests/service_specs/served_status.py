"""Helpers shared by the tests of the dual-base exceptions and the translator."""

from __future__ import annotations

from typing import Any

from django.core.handlers.exception import convert_exception_to_response
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView


def served_status(error: Exception) -> int:
    """The status Django answers when a DRF view raises ``error``.

    Served through DRF's exception handler and then Django's own conversion of
    an exception to a response, which is what wraps every view in a running
    project. So an exception DRF's handler does not recognise is answered as a
    project answers it, a 500, rather than escaping the test as a raise.
    """

    class _Raising(APIView):
        authentication_classes: Any = []
        permission_classes: Any = []

        def get(self, request: Request) -> Response:
            raise error

    view = convert_exception_to_response(_Raising.as_view())
    return view(APIRequestFactory().get("/")).status_code
