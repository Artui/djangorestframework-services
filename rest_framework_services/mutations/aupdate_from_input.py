"""Async equivalent of
[`update_from_input`][rest_framework_services.mutations.update_from_input.update_from_input]."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.mutations.utils import (
    _auto_now_field_names,
    aapply_forward_relations,
    aapply_m2m,
    aapply_relations,
    am2m_changes,
    coerce_to_dict,
    diff_attrs,
    extract_relation_data,
    filter_input,
    merge_relations,
    reject_m2m_overlap,
    resolve_update_fields,
    sync_relation_cache,
)
from rest_framework_services.types.change_result import ChangeResult, ModelT
from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.relation_spec import RelationSpec


async def aupdate_from_input(
    instance: ModelT,
    data: Any,
    *,
    field_map: dict[str, str] | None = None,
    exclude_fields: list[str] | None = None,
    m2m: dict[str, Any] | None = None,
    update_fields: bool | list[str] = True,
    children: Mapping[str, ChildSpec] | None = None,
    relations: Mapping[str, RelationSpec] | None = None,
    context: Mapping[str, Any] | None = None,
) -> ChangeResult[ModelT]:
    """Async sibling of
    [`update_from_input`][rest_framework_services.mutations.update_from_input.update_from_input]
    using ``asave()``/``aset()``."""
    relation_specs = merge_relations(children, relations)
    reject_m2m_overlap(m2m, relation_specs)
    raw: dict[str, Any] = coerce_to_dict(data)
    relation_data: dict[str, Any] = extract_relation_data(raw, relation_specs)
    new_values: dict[str, Any] = filter_input(
        raw,
        field_map=field_map,
        exclude_fields=exclude_fields,
    )
    forward_values, forward_changes = await aapply_forward_relations(
        relation_data, relation_specs, context=context
    )
    new_values.update(forward_values)
    field_changes = diff_attrs(instance, new_values)
    for change in field_changes:
        setattr(instance, change.field, change.new)
    for relation, target in forward_values.items():
        sync_relation_cache(instance, relation, target)
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
    child_changes, related_changes = await aapply_relations(
        instance, relation_data, relation_specs, created=False, context=context
    )
    return ChangeResult(
        instance=instance,
        created=False,
        changes=field_changes + m2m_field_changes,
        children=child_changes,
        relations=forward_changes + related_changes,
    )
