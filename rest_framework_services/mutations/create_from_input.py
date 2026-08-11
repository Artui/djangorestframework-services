"""Construct, save, and post-process a new model instance from input data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.mutations.utils import (
    apply_forward_relations,
    apply_m2m,
    apply_relations,
    changes_for_create,
    coerce_to_dict,
    extract_relation_data,
    filter_input,
    m2m_changes,
    merge_relations,
)
from rest_framework_services.types.change_result import ChangeResult, ModelT
from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.relation_spec import RelationSpec


def create_from_input(
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
    """Build, ``save()``, and return a fresh instance of ``model``.

    Regular fields come from ``data`` (a dataclass, dict, or object with
    ``__dict__``); M2M assignments are applied post-save via the ``m2m``
    kwarg, mapping attribute name to the value to ``set()``.

    ``relations`` maps a relation name to the spec for its kind; the nested
    payload is read from ``data[relation]`` and written in the order the kind
    dictates, not the order the map is spelled in (see
    :class:`~rest_framework_services.RelationPhase`). ``children`` is the
    reverse-FK alias — the same map under the name it shipped as — and a name
    declared in both raises. Keep the whole call inside the service's atomic
    block.

    ``context`` is an **opaque** mapping forwarded verbatim into the pool of
    any per-child service a :class:`~rest_framework_services.ChildSpec`
    declares. This helper never reads it — it exists so per-row work
    downstream can see the acting caller. The default model-service factories
    populate it from the framework's kwargs pool automatically; a hand-written
    service opts in with ``context=kwargs``.
    """
    relation_specs = merge_relations(children, relations)
    raw: dict[str, Any] = coerce_to_dict(data)
    relation_data: dict[str, Any] = extract_relation_data(raw, relation_specs)
    new_values: dict[str, Any] = filter_input(
        raw,
        field_map=field_map,
        exclude_fields=exclude_fields,
    )
    forward_values, forward_changes = apply_forward_relations(
        relation_data, relation_specs, context=context
    )
    new_values.update(forward_values)
    instance: ModelT = model(**new_values)
    instance.save()
    field_changes = changes_for_create(new_values)
    m2m_field_changes, to_apply = m2m_changes(instance, m2m, created=True)
    apply_m2m(instance, to_apply)
    child_changes, related_changes = apply_relations(
        instance, relation_data, relation_specs, created=True, context=context
    )
    return ChangeResult(
        instance=instance,
        created=True,
        changes=field_changes + m2m_field_changes,
        children=child_changes,
        relations=forward_changes + related_changes,
    )
