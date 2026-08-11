"""Async equivalent of :func:`create_from_input`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.mutations.utils import (
    aapply_children,
    aapply_m2m,
    am2m_changes,
    changes_for_create,
    coerce_to_dict,
    extract_children,
    filter_input,
)
from rest_framework_services.types.change_result import ChangeResult, ModelT
from rest_framework_services.types.child_spec import ChildSpec


async def acreate_from_input(
    model: type[ModelT],
    data: Any,
    *,
    field_map: dict[str, str] | None = None,
    exclude_fields: list[str] | None = None,
    m2m: dict[str, Any] | None = None,
    children: Mapping[str, ChildSpec] | None = None,
    context: Mapping[str, Any] | None = None,
) -> ChangeResult[ModelT]:
    """Async sibling of :func:`create_from_input` using ``asave()``/``aset()``."""
    raw: dict[str, Any] = coerce_to_dict(data)
    child_data: dict[str, Any] = extract_children(raw, children)
    new_values: dict[str, Any] = filter_input(
        raw,
        field_map=field_map,
        exclude_fields=exclude_fields,
    )
    instance: ModelT = model(**new_values)
    await instance.asave()
    field_changes = changes_for_create(new_values)
    m2m_field_changes, to_apply = await am2m_changes(instance, m2m, created=True)
    await aapply_m2m(instance, to_apply)
    child_changes = (
        await aapply_children(instance, child_data, children, created=True, context=context)
        if children
        else ()
    )
    return ChangeResult(
        instance=instance,
        created=True,
        changes=field_changes + m2m_field_changes,
        children=child_changes,
    )
