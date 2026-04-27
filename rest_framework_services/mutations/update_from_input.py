"""Apply partial input to an existing instance and persist only changed fields."""

from __future__ import annotations

from typing import Any

from django.db.models import Model

from rest_framework_services.mutations.utils import (
    apply_m2m,
    coerce_to_dict,
    diff_attrs,
    filter_input,
    m2m_changes,
    resolve_update_fields,
)
from rest_framework_services.types.change_result import ChangeResult


def update_from_input(
    instance: Model,
    data: Any,
    *,
    field_map: dict[str, str] | None = None,
    exclude_fields: list[str] | None = None,
    m2m: dict[str, Any] | None = None,
    update_fields: bool | list[str] = True,
) -> ChangeResult:
    """Update ``instance`` with values from ``data``, persisting only deltas.

    By default (``update_fields=True``), the save call uses
    ``update_fields=<changed>`` to write the minimal set of columns. Pass
    ``False`` to perform a full save, or an explicit list to override.
    """
    raw: dict[str, Any] = coerce_to_dict(data)
    new_values: dict[str, Any] = filter_input(
        raw,
        field_map=field_map,
        exclude_fields=exclude_fields,
    )
    field_changes = diff_attrs(instance, new_values)
    for change in field_changes:
        setattr(instance, change.field, change.new)
    m2m_field_changes, to_apply = m2m_changes(instance, m2m, created=False)
    changed_field_names: tuple[str, ...] = tuple(change.field for change in field_changes)
    save_fields: list[str] | None = resolve_update_fields(update_fields, changed_field_names)
    if field_changes or update_fields is False:
        if save_fields is None:
            instance.save()
        else:
            instance.save(update_fields=save_fields)
    apply_m2m(instance, to_apply)
    return ChangeResult(
        instance=instance,
        created=False,
        changes=field_changes + m2m_field_changes,
    )
