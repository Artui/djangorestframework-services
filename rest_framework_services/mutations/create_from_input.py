"""Construct, save, and post-process a new model instance from input data."""

from __future__ import annotations

from typing import Any

from django.db.models import Model

from rest_framework_services.mutations.utils import (
    apply_m2m,
    changes_for_create,
    coerce_to_dict,
    filter_input,
    m2m_changes,
)
from rest_framework_services.types.change_result import ChangeResult


def create_from_input(
    model: type[Model],
    data: Any,
    *,
    field_map: dict[str, str] | None = None,
    exclude_fields: list[str] | None = None,
    m2m: dict[str, Any] | None = None,
) -> ChangeResult:
    """Build, ``save()``, and return a fresh instance of ``model``.

    Regular fields come from ``data`` (a dataclass, dict, or object with
    ``__dict__``); M2M assignments are applied post-save via the ``m2m``
    kwarg, mapping attribute name to the value to ``set()``.
    """
    raw: dict[str, Any] = coerce_to_dict(data)
    new_values: dict[str, Any] = filter_input(
        raw,
        field_map=field_map,
        exclude_fields=exclude_fields,
    )
    instance: Model = model(**new_values)
    instance.save()
    field_changes = changes_for_create(new_values)
    m2m_field_changes, to_apply = m2m_changes(instance, m2m, created=True)
    apply_m2m(instance, to_apply)
    return ChangeResult(
        instance=instance,
        created=True,
        changes=field_changes + m2m_field_changes,
    )
