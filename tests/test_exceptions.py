"""The exceptions package: the kernel's classes, at drfs' paths.

Every class here is django-service-specs' own object (``test_kernel_reexports.py``
holds the identity), so its behaviour is tested there. What stays here is what a
drfs caller sees change because the class did, and the package's one boundary.
"""

from __future__ import annotations

import re

from django.utils.translation import gettext_lazy

from rest_framework_services.exceptions import (
    action_unavailable,
    additional_input_required,
    service_conflict,
    service_error,
    service_not_found,
    service_validation_error,
)
from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)


def test_a_lazy_translation_is_the_message() -> None:
    """A ``gettext_lazy`` message is the message, where drfs once took only a
    ``str`` and answered anything else with the default."""
    error = ServiceValidationError(gettext_lazy("That slot is taken."))

    assert str(error.message) == "That slot is taken."
    assert str(error) == "That slot is taken."
    assert str(error) != ServiceValidationError.default_message


def test_no_module_imports_drf() -> None:
    # Match `from rest_framework`/`import rest_framework` only when the
    # next character is NOT `_` (which would mean rest_framework_services).
    forbidden = re.compile(r"\b(?:from|import)\s+rest_framework(?!\w)")
    for module in (
        action_unavailable,
        additional_input_required,
        service_conflict,
        service_error,
        service_not_found,
        service_validation_error,
    ):
        source = module.__file__
        assert source is not None
        with open(source, encoding="utf-8") as fh:
            contents = fh.read()
        assert forbidden.search(contents) is None
