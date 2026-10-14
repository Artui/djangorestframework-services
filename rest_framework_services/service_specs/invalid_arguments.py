"""``InvalidArguments`` - the kernel's refusal of the arguments, which DRF answers 400."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from rest_framework.exceptions import ValidationError


class InvalidArguments(KernelInvalidArguments, ValidationError):
    """The arguments were refused, as both the kernel's ``InvalidArguments`` and DRF's ``ValidationError``.

    Every published consumer of this package catches DRF's ``ValidationError``
    by name - an MCP server answering "invalid arguments", an agent tool asking
    the model to retry - and DRF's exception handler answers it 400. None of
    them catches the kernel's class, so what dispatch refuses leaves as this,
    and an ``except`` written against either side catches it.

    Deliberately **not** a service validation error, which the kernel's class
    is not either: a business rule stated by a service about well-shaped input
    is a different refusal, and an MCP server answers the two differently.

    ``detail`` is the kernel's tree - fields, nested objects, rows under their
    ``int`` index - with every message an ``ErrorDetail``. A message that
    carries a code (the kernel's ``CodedMessage`` does) keeps it, and one
    that carries none takes ``code``, ``"invalid"`` unless given.

    **DRF's ``__init__`` runs on purpose.** By the order of the bases the
    kernel's runs, and it leaves ``detail`` as plain strings: DRF's handler
    still answers 400 with a byte-identical body, but ``get_codes()`` raises,
    so a test asserting the body passes on the broken class.
    ``test_get_codes_keeps_the_kernels_code_and_defaults_the_rest`` and
    ``test_every_message_is_an_error_detail`` are what fail without it.
    """

    def __init__(self, detail: Mapping[Any, Any], code: str | None = None) -> None:
        # The kernel's first, for ``message`` and whatever else it sets; its
        # ``super()`` chain reaches DRF's with the default message, and the
        # kernel then overwrites ``detail`` with plain strings, which DRF's run
        # here replaces with the same tree as ``ErrorDetail``s.
        KernelInvalidArguments.__init__(self, detail)
        ValidationError.__init__(self, self.detail, code)
