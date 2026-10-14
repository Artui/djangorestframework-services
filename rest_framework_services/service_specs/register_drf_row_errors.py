"""``register_drf_row_errors`` - DRF's ``ValidationError`` from a nested row lands at that row."""

from __future__ import annotations

from typing import Any

from django_service_specs.mutations.register_row_error import register_row_error
from rest_framework.exceptions import ValidationError


def register_drf_row_errors() -> None:
    """Register DRF's ``ValidationError`` as a refusal the kernel re-roots under the row that raised it.

    A nested write runs a row service per related row, and one written for DRF
    refuses its row with DRF's ``ValidationError``, which the kernel cannot
    name. Unregistered, that refusal leaves the write carrying only the row's
    own fields, so the caller cannot tell which row was refused. Registered,
    it is re-addressed as the kernel's own refusals are - a collection's row
    under its ``int`` index, a message about the row itself under
    ``non_field_errors`` - and rebuilt as DRF's ``ValidationError``, with every
    message's code kept, so DRF still answers it 400.

    Process-wide and for the life of the process, as the kernel's registry is.
    Running it again does nothing, because the reader is one module-level
    function; a second registration of the class with any other reader is
    refused by the kernel.

    Not a home for this package's dual-base ``InvalidArguments``: the kernel
    reads its own ``InvalidArguments`` before any registered class, so that
    one leaves a nested row as the kernel's class, and reaches DRF's ladders
    through
    [`translate_dispatch_errors`][rest_framework_services.service_specs.translate_dispatch_errors.translate_dispatch_errors].
    """
    register_row_error(ValidationError, _validation_error_detail)


def _validation_error_detail(exc: ValidationError) -> Any:
    """DRF's detail as it stands: a field map or a list of the row's own messages, codes and all."""
    return exc.detail
