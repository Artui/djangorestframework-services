"""``DRFServiceValidationError`` - a service's DRF ``ValidationError`` as the kernel's service validation error."""

from __future__ import annotations

from typing import Any

from django_service_specs.services.service_validation_error import (
    ServiceValidationError as KernelServiceValidationError,
)
from rest_framework.exceptions import ValidationError


class DRFServiceValidationError(KernelServiceValidationError, ValidationError):
    """A service refused its input, as both the kernel's ``ServiceValidationError`` and DRF's ``ValidationError``.

    What an author callable's DRF ``ValidationError`` leaves the kernel route
    as, through
    [`translate_drf_refusals`][rest_framework_services.service_specs.translate_drf_refusals.translate_drf_refusals]:
    a service, a selector, a precondition, a provider or an
    ``extend_queryset`` refusing well-shaped input by a rule of its own. A
    transport reading the kernel's classes answers it as the service's
    refusal, and an ``except`` written against DRF's still catches it.

    Named apart from the package root's ``ServiceValidationError``, which is
    the kernel's own class re-exported, so neither is mistaken for the
    other. Deliberately **not** an ``InvalidArguments``: the kernel's refusal
    of the arguments' shape is a different refusal, which an MCP server
    answers differently.

    ``detail`` is DRF's, any shape, with every message an ``ErrorDetail``
    keeping its code; ``message`` is the kernel's, the detail where that is a
    string and the kernel's default otherwise. DRF makes a list of a single
    message, so a refusal translated from DRF's carries the default
    (test_a_translated_message_is_the_kernel_s_default).

    Both ``__init__``s run by name, as ``NotPermitted``'s do. The kernel's
    sets ``message``: test_a_string_detail_is_the_message fails without it.
    DRF's makes the detail ``ErrorDetail``s with the ``code`` given, which the
    kernel's ``super()`` chain never passes: test_a_code_given_is_kept fails
    without it.
    """

    def __init__(self, detail: Any, code: str | None = None) -> None:
        KernelServiceValidationError.__init__(self, detail)
        ValidationError.__init__(self, detail, code)
