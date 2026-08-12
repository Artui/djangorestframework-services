"""Validation error raised from service code."""

from __future__ import annotations

from typing import Any

from rest_framework_services.exceptions.service_error import ServiceError


class ServiceValidationError(ServiceError):
    """Raised by services to signal invalid input or invalid state.

    Distinct from :class:`ServiceError` so the view boundary maps it to a DRF
    ``ValidationError`` (HTTP 400) where ``ServiceError`` maps to 422.
    ``detail`` may be a string, a dict (field → error(s)), or a list of errors,
    mirroring DRF's own ``ValidationError`` payload shapes.
    """

    default_message: str = "Service validation error."

    def __init__(self, detail: str | dict[str, Any] | list[Any]) -> None:
        message: str = detail if isinstance(detail, str) else self.default_message
        super().__init__(message)
        self.detail: str | dict[str, Any] | list[Any] = detail
