"""In-memory attribute application — no save, no DB access."""

from __future__ import annotations

from typing import Any

from django.db.models import Model

from rest_framework_services.mutations.utils import (
    coerce_to_dict,
    diff_attrs,
    filter_input,
)
from rest_framework_services.types.change_result import ChangeResult


def apply_input(
    instance: Model,
    data: Any,
    *,
    field_map: dict[str, str] | None = None,
    exclude_fields: list[str] | None = None,
) -> ChangeResult:
    """Set attributes from ``data`` onto ``instance`` without saving.

    Returns a [`ChangeResult`][rest_framework_services.types.change_result.ChangeResult] describing fields whose value actually
    changed. Useful when a service wants to inspect what the input would
    change before deciding whether to persist.
    """
    raw: dict[str, Any] = coerce_to_dict(data)
    new_values: dict[str, Any] = filter_input(
        raw,
        field_map=field_map,
        exclude_fields=exclude_fields,
    )
    changes = diff_attrs(instance, new_values)
    for change in changes:
        setattr(instance, change.field, change.new)
    return ChangeResult(instance=instance, created=False, changes=changes)
