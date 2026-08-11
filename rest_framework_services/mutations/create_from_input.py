"""Construct, save, and post-process a new model instance from input data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.mutations.utils import (
    apply_children,
    apply_m2m,
    changes_for_create,
    coerce_to_dict,
    extract_children,
    filter_input,
    m2m_changes,
)
from rest_framework_services.types.change_result import ChangeResult, ModelT
from rest_framework_services.types.child_spec import ChildSpec


def create_from_input(
    model: type[ModelT],
    data: Any,
    *,
    field_map: dict[str, str] | None = None,
    exclude_fields: list[str] | None = None,
    m2m: dict[str, Any] | None = None,
    children: Mapping[str, ChildSpec] | None = None,
    context: Mapping[str, Any] | None = None,
) -> ChangeResult[ModelT]:
    """Build, ``save()``, and return a fresh instance of ``model``.

    Regular fields come from ``data`` (a dataclass, dict, or object with
    ``__dict__``); M2M assignments are applied post-save via the ``m2m``
    kwarg, mapping attribute name to the value to ``set()``.

    ``children`` maps a reverse-FK relation name to a
    :class:`~rest_framework_services.ChildSpec`; the matching child rows are
    read from ``data[relation]`` and persisted post-save (recursively). Keep
    the whole call inside the service's atomic block.

    ``context`` is an **opaque** mapping forwarded verbatim into the pool of
    any per-child service a :class:`~rest_framework_services.ChildSpec`
    declares. This helper never reads it — it exists so per-row work
    downstream can see the acting caller. The default model-service factories
    populate it from the framework's kwargs pool automatically; a hand-written
    service opts in with ``context=kwargs``.
    """
    raw: dict[str, Any] = coerce_to_dict(data)
    child_data: dict[str, Any] = extract_children(raw, children)
    new_values: dict[str, Any] = filter_input(
        raw,
        field_map=field_map,
        exclude_fields=exclude_fields,
    )
    instance: ModelT = model(**new_values)
    instance.save()
    field_changes = changes_for_create(new_values)
    m2m_field_changes, to_apply = m2m_changes(instance, m2m, created=True)
    apply_m2m(instance, to_apply)
    child_changes = (
        apply_children(instance, child_data, children, created=True, context=context)
        if children
        else ()
    )
    return ChangeResult(
        instance=instance,
        created=True,
        changes=field_changes + m2m_field_changes,
        children=child_changes,
    )
