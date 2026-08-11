"""Apply partial input to an existing instance and persist only changed fields."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.mutations.utils import (
    _auto_now_field_names,
    apply_children,
    apply_m2m,
    coerce_to_dict,
    diff_attrs,
    extract_children,
    filter_input,
    m2m_changes,
    resolve_update_fields,
)
from rest_framework_services.types.change_result import ChangeResult, ModelT
from rest_framework_services.types.child_spec import ChildSpec


def update_from_input(
    instance: ModelT,
    data: Any,
    *,
    field_map: dict[str, str] | None = None,
    exclude_fields: list[str] | None = None,
    m2m: dict[str, Any] | None = None,
    update_fields: bool | list[str] = True,
    children: Mapping[str, ChildSpec] | None = None,
    context: Mapping[str, Any] | None = None,
) -> ChangeResult[ModelT]:
    """Update ``instance`` with values from ``data``, persisting only deltas.

    By default (``update_fields=True``), the save call uses
    ``update_fields=<changed>`` to write the minimal set of columns.
    ``auto_now=True`` fields (e.g. ``updated_at``) are automatically added to
    that list so they are refreshed alongside the mutation. Pass ``False`` to
    perform a full save, or an explicit list to control exactly which columns
    are written (no auto-injection in that case).

    ``children`` maps a reverse-FK relation name to a
    :class:`~rest_framework_services.ChildSpec`. The child rows from
    ``data[relation]`` are reconciled with the existing collection
    (create / update / orphan-remove per the spec's ``mode``); a relation the
    input omits is left untouched. Keep the call inside the service's atomic
    block.

    ``context`` is an **opaque** mapping forwarded verbatim into the pool of
    any per-child service a :class:`~rest_framework_services.ChildSpec`
    declares; this helper never reads it. See
    :func:`~rest_framework_services.create_from_input`.
    """
    raw: dict[str, Any] = coerce_to_dict(data)
    child_data: dict[str, Any] = extract_children(raw, children)
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
    save_fields: list[str] | None = resolve_update_fields(
        update_fields, changed_field_names, _auto_now_field_names(instance)
    )
    if field_changes or update_fields is False:
        if save_fields is None:
            instance.save()
        else:
            instance.save(update_fields=save_fields)
    apply_m2m(instance, to_apply)
    child_changes = (
        apply_children(instance, child_data, children, created=False, context=context)
        if children
        else ()
    )
    return ChangeResult(
        instance=instance,
        created=False,
        changes=field_changes + m2m_field_changes,
        children=child_changes,
    )
