"""``ValueFormatter``: what a drfs caller sees change now that it is the kernel's.

The transform, its schema and ``timestamp()`` are django-service-specs' and
tested there. These pin the two differences from drfs' own class.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.types.value_formatter import ValueFormatter


def test_a_timestamp_naming_no_real_moment_is_reported_as_stored() -> None:
    """Shaped like a date-time, so Django's parser tries it and raises on the
    day. One bad row is reported as it was stored, rather than the parser's
    ``ValueError`` failing the whole page that carries it."""
    assert ValueFormatter.timestamp().apply("2026-02-30T12:00:00Z") == "2026-02-30T12:00:00Z"


def test_the_fragment_naming_the_type_is_refused_in_the_kernels_words() -> None:
    with pytest.raises(ImproperlyConfigured, match="may not set 'type' - produces= is"):
        ValueFormatter(str, "string", {"type": "integer"})
