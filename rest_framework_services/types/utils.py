"""Internal helpers shared across the ``types`` package."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured


def validate_metadata(metadata: Any, *, label: str) -> None:
    """Reject a non-mapping ``metadata`` declaration on a spec.

    Called from ``__post_init__``, unlike every other spec field — those are
    checked at ``as_view()`` time by
    :mod:`rest_framework_services.views.spec_validation`. That is deliberate:
    ``metadata`` exists to be read by consumer code that may never mount the
    spec on a view at all (a registry consumer, :func:`dispatch_spec`, an MCP
    or Pydantic-AI binding), so a view-time check would skip exactly the paths
    the field is for.

    Shape-only by design. The framework never looks inside the mapping.
    """
    if metadata is None or isinstance(metadata, Mapping):
        return
    raise ImproperlyConfigured(
        f"{label}.metadata must be a mapping (or None); got "
        f"{type(metadata).__name__}. The framework never reads its contents, "
        f"but the shape is fixed so consumers can rely on it."
    )
