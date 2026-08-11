"""Async equivalent of :func:`update_from_input`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.mutations.utils import (
    _auto_now_field_names,
    aapply_children,
    aapply_m2m,
    am2m_changes,
    coerce_to_dict,
    diff_attrs,
    extract_children,
    filter_input,
    resolve_update_fields,
)
from rest_framework_services.types.change_result import ChangeResult, ModelT
from rest_framework_services.types.child_spec import ChildSpec


async def aupdate_from_input(
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
    """Async sibling of :func:`update_from_input` using ``asave()``/``aset()``."""
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
    m2m_field_changes, to_apply = await am2m_changes(instance, m2m, created=False)
    changed_field_names: tuple[str, ...] = tuple(change.field for change in field_changes)
    save_fields: list[str] | None = resolve_update_fields(
        update_fields, changed_field_names, _auto_now_field_names(instance)
    )
    if field_changes or update_fields is False:
        if save_fields is None:
            await instance.asave()
        else:
            await instance.asave(update_fields=save_fields)
    await aapply_m2m(instance, to_apply)
    child_changes = (
        await aapply_children(instance, child_data, children, created=False, context=context)
        if children
        else ()
    )
    return ChangeResult(
        instance=instance,
        created=False,
        changes=field_changes + m2m_field_changes,
        children=child_changes,
    )
