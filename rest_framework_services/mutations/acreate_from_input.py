"""Async equivalent of
[`create_from_input`][rest_framework_services.mutations.create_from_input.create_from_input]."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.mutations.utils import (
    aapply_forward_relations,
    aapply_m2m,
    aapply_relations,
    am2m_changes,
    changes_for_create,
    coerce_to_dict,
    extract_relation_data,
    filter_input,
    merge_relations,
    reject_m2m_overlap,
)
from rest_framework_services.types.change_result import ChangeResult, ModelT
from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.relation_spec import RelationSpec


async def acreate_from_input(
    model: type[ModelT],
    data: Any,
    *,
    field_map: dict[str, str] | None = None,
    exclude_fields: list[str] | None = None,
    m2m: dict[str, Any] | None = None,
    children: Mapping[str, ChildSpec] | None = None,
    relations: Mapping[str, RelationSpec] | None = None,
    context: Mapping[str, Any] | None = None,
) -> ChangeResult[ModelT]:
    """Async sibling of
    [`create_from_input`][rest_framework_services.mutations.create_from_input.create_from_input]
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
    instance: ModelT = model(**new_values)
    await instance.asave()
    field_changes = changes_for_create(new_values)
    m2m_field_changes, to_apply = await am2m_changes(instance, m2m, created=True)
    await aapply_m2m(instance, to_apply)
    child_changes, related_changes = await aapply_relations(
        instance, relation_data, relation_specs, created=True, context=context
    )
    return ChangeResult(
        instance=instance,
        created=True,
        changes=field_changes + m2m_field_changes,
        children=child_changes,
        relations=forward_changes + related_changes,
    )
