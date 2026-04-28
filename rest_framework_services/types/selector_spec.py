"""``SelectorSpec`` — bundles per-action configuration for read actions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rest_framework.serializers import Serializer


@dataclass(frozen=True)
class SelectorSpec:
    """All wiring for a single read action in one record.

    Used as a value in ``action_specs`` on viewsets and as the ``spec=``
    argument to :class:`SelectorListView` / :class:`SelectorRetrieveView`.

    Fields:

    - **``selector``** — callable invoked by ``get_queryset()`` (list) or
      ``get_object()`` (retrieve). ``None`` means "use the configured
      ``queryset`` / default DRF behaviour".
    - **``output_serializer``** — DRF ``Serializer`` subclass used by
      ``get_serializer_class()`` for this action. ``None`` falls back to
      DRF's standard ``serializer_class``.
    """

    selector: Callable[..., Any] | None = None
    output_serializer: type[Serializer] | None = None
