"""Internal helpers shared by the mutation functions.

Nothing in this module is exported from the package's public API.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Model

from rest_framework_services.types.child_collection_change import ChildCollectionChange
from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.field_change import FieldChange
from rest_framework_services.types.unset import UNSET


def coerce_to_dict(data: Any) -> dict[str, Any]:
    """Normalize input ``data`` to a dict mapping field name to value.

    Accepts ``None``, a plain dict, a dataclass instance, or any object with
    a ``__dict__``. Raises ``TypeError`` for anything else.
    """
    if data is None:
        return {}
    if isinstance(data, dict):
        return dict(data)
    if is_dataclass(data) and not isinstance(data, type):
        return {f.name: getattr(data, f.name) for f in fields(data)}
    if hasattr(data, "__dict__"):
        return dict(vars(data))
    raise TypeError(
        f"Cannot coerce input of type {type(data).__name__!r} to a dict; "
        "expected None, a dict, a dataclass instance, or an object with __dict__."
    )


def filter_input(
    raw: dict[str, Any],
    *,
    field_map: dict[str, str] | None,
    exclude_fields: list[str] | None,
) -> dict[str, Any]:
    """Apply ``field_map`` and ``exclude_fields``, dropping ``UNSET`` values.

    ``exclude_fields`` is matched against **input** field names (pre-mapping)
    so callers can exclude in the vocabulary they passed in.
    """
    excluded: set[str] = set(exclude_fields or ())
    result: dict[str, Any] = {}
    mapping: dict[str, str] = field_map or {}
    for key, value in raw.items():
        if value is UNSET:
            continue
        if key in excluded:
            continue
        result[mapping.get(key, key)] = value
    return result


def safe_getattr(instance: Model, attr: str) -> Any:
    """``getattr`` that returns ``UNSET`` for unset relations or missing attrs."""
    try:
        return getattr(instance, attr, UNSET)
    except ObjectDoesNotExist:
        return UNSET


def diff_attrs(
    instance: Model,
    new_values: dict[str, Any],
) -> tuple[FieldChange, ...]:
    """Return the subset of ``new_values`` whose value differs from current."""
    changes: list[FieldChange] = []
    for attr, new_value in new_values.items():
        old_value: Any = safe_getattr(instance, attr)
        if old_value != new_value:
            changes.append(FieldChange(field=attr, old=old_value, new=new_value))
    return tuple(changes)


def m2m_current_pks(instance: Model, attr: str) -> list[Any]:
    """Return primary keys of the current many-to-many members for ``attr``."""
    manager: Any = getattr(instance, attr)
    return list(manager.values_list("pk", flat=True))


async def am2m_current_pks(instance: Model, attr: str) -> list[Any]:
    """Async variant of :func:`m2m_current_pks`."""
    manager: Any = getattr(instance, attr)
    return [pk async for pk in manager.values_list("pk", flat=True)]


def m2m_target_pks(value: Any) -> list[Any]:
    """Best-effort extraction of primary keys from an M2M assignment value."""
    items: list[Any] = list(value) if value is not None else []
    pks: list[Any] = []
    for item in items:
        if isinstance(item, Model):
            pks.append(item.pk)
        else:
            pks.append(item)
    return pks


def m2m_changes(
    instance: Model,
    m2m: dict[str, Any] | None,
    *,
    created: bool,
) -> tuple[tuple[FieldChange, ...], dict[str, Any]]:
    """Compute :class:`FieldChange` entries and the to-apply m2m dict.

    Returns ``(changes, to_apply)`` where ``to_apply`` is the subset of m2m
    assignments that should actually be persisted (everything for creates,
    only differing relations for updates).
    """
    if not m2m:
        return ((), {})
    changes: list[FieldChange] = []
    to_apply: dict[str, Any] = {}
    for attr, value in m2m.items():
        new_pks: list[Any] = m2m_target_pks(value)
        if created:
            changes.append(FieldChange(field=attr, old=UNSET, new=value))
            to_apply[attr] = value
            continue
        old_pks: list[Any] = m2m_current_pks(instance, attr)
        if sorted(old_pks, key=repr) != sorted(new_pks, key=repr):
            changes.append(FieldChange(field=attr, old=old_pks, new=value))
            to_apply[attr] = value
    return (tuple(changes), to_apply)


async def am2m_changes(
    instance: Model,
    m2m: dict[str, Any] | None,
    *,
    created: bool,
) -> tuple[tuple[FieldChange, ...], dict[str, Any]]:
    """Async variant of :func:`m2m_changes`."""
    if not m2m:
        return ((), {})
    changes: list[FieldChange] = []
    to_apply: dict[str, Any] = {}
    for attr, value in m2m.items():
        new_pks: list[Any] = m2m_target_pks(value)
        if created:
            changes.append(FieldChange(field=attr, old=UNSET, new=value))
            to_apply[attr] = value
            continue
        old_pks: list[Any] = await am2m_current_pks(instance, attr)
        if sorted(old_pks, key=repr) != sorted(new_pks, key=repr):
            changes.append(FieldChange(field=attr, old=old_pks, new=value))
            to_apply[attr] = value
    return (tuple(changes), to_apply)


def _normalize_m2m_value(value: Any) -> list[Any]:
    """Coerce a m2m assignment value to a concrete list (``None`` → ``[]``)."""
    if value is None:
        return []
    return list(value)


def apply_m2m(instance: Model, to_apply: dict[str, Any]) -> None:
    """Set each ``to_apply`` value on the instance's m2m manager (sync)."""
    for attr, value in to_apply.items():
        manager: Any = getattr(instance, attr)
        manager.set(_normalize_m2m_value(value))


async def aapply_m2m(instance: Model, to_apply: dict[str, Any]) -> None:
    """Set each ``to_apply`` value on the instance's m2m manager (async)."""
    for attr, value in to_apply.items():
        manager: Any = getattr(instance, attr)
        await manager.aset(_normalize_m2m_value(value))


def changes_for_create(new_values: dict[str, Any]) -> tuple[FieldChange, ...]:
    """Build :class:`FieldChange` entries for every assigned create field."""
    return tuple(
        FieldChange(field=attr, old=UNSET, new=value) for attr, value in new_values.items()
    )


def _auto_now_field_names(instance: Model) -> tuple[str, ...]:
    """Return the names of all ``auto_now=True`` fields on the model."""
    return tuple(
        f.name for f in instance._meta.concrete_fields if hasattr(f, "auto_now") and f.auto_now
    )


def resolve_update_fields(
    update_fields: bool | list[str],
    changed: tuple[str, ...],
    auto_now_fields: tuple[str, ...] = (),
) -> list[str] | None:
    """Map the public ``update_fields`` argument to a ``save()``-compatible list.

    - ``True`` (default) → ``list(changed)`` extended with any ``auto_now=True``
      fields so timestamp columns are refreshed alongside the mutation. Returns
      ``None`` if nothing changed (save skipped entirely).
    - ``False`` → ``None`` (full save; Django handles ``auto_now`` automatically).
    - explicit list → returned as-is (caller controls which columns are written).
    """
    if update_fields is True:
        fields = list(changed)
        if fields:
            for f in auto_now_fields:
                if f not in fields:
                    fields.append(f)
        return fields or None
    if update_fields is False:
        return None
    return list(update_fields)


# --- reverse-FK child collections (NEST) ---------------------------------


def extract_children(
    raw: dict[str, Any],
    children: Mapping[str, ChildSpec] | None,
) -> dict[str, Any]:
    """Pop each child-relation key out of ``raw`` and return ``{relation: value}``.

    Removing the keys keeps the child lists out of the scalar field set the
    parent's ``create``/``update`` would otherwise try to assign. A relation
    the input omitted entirely maps to ``UNSET`` (left untouched by the write);
    an explicit empty list maps to ``[]`` (processed — in ``replace`` mode that
    removes every existing child).
    """
    if not children:
        return {}
    return {name: raw.pop(name, UNSET) for name in children}


def _child_fk_nullable(spec: ChildSpec) -> bool:
    """Whether the child's foreign key to the parent allows ``NULL``."""
    return bool(spec.model._meta.get_field(spec.fk).null)


def remove_child(child: Model, fk: str, *, nullable: bool) -> tuple[str, Any]:
    """Detach (nullable FK → ``SET_NULL``) or delete (else → ``CASCADE``) ``child``.

    Returns ``("unlinked" | "deleted", pk)`` with the pk captured *before* any
    delete (Django clears ``instance.pk`` afterwards).
    """
    pk = child.pk
    if nullable:
        setattr(child, fk, None)
        child.save(update_fields=[fk])
        return ("unlinked", pk)
    child.delete()
    return ("deleted", pk)


async def aremove_child(child: Model, fk: str, *, nullable: bool) -> tuple[str, Any]:
    """Async variant of :func:`remove_child`."""
    pk = child.pk
    if nullable:
        setattr(child, fk, None)
        await child.asave(update_fields=[fk])
        return ("unlinked", pk)
    await child.adelete()
    return ("deleted", pk)


def apply_children(
    parent: Model,
    child_data: dict[str, Any],
    children: Mapping[str, ChildSpec],
    *,
    created: bool,
) -> tuple[ChildCollectionChange, ...]:
    """Persist each reverse-FK child collection declared in ``children``.

    For each relation: match incoming rows to existing children by the spec's
    ``match_key`` (skipped on ``created`` — a fresh parent has none), update
    matches, create the rest, and — in ``replace`` mode — remove orphans via
    :func:`remove_child`. Each child runs back through ``create``/``update``
    so scalar / m2m / nested semantics compose recursively.
    """
    # Lazy import: genuine recursion cycle — the parent helpers call this, and
    # this calls them again for each child (and grandchild).
    from rest_framework_services.mutations.create_from_input import create_from_input
    from rest_framework_services.mutations.update_from_input import update_from_input

    deltas: list[ChildCollectionChange] = []
    for relation, spec in children.items():
        items = child_data.get(relation, UNSET)
        if items is UNSET:
            deltas.append(ChildCollectionChange(relation=relation))
            continue
        existing_by_key: dict[Any, Model] = (
            {}
            if created
            else {getattr(e, spec.match_key): e for e in getattr(parent, relation).all()}
        )
        created_pks: list[Any] = []
        updated_pks: list[Any] = []
        matched: set[Any] = set()
        for item in (coerce_to_dict(i) for i in (items or [])):
            child_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
            key = item.get(spec.match_key)
            if key is not None and key in existing_by_key:
                child = existing_by_key[key]
                update_from_input(
                    child,
                    item,
                    field_map=spec.field_map,
                    exclude_fields=spec.exclude_fields,
                    m2m=child_m2m,
                    children=spec.children,
                )
                updated_pks.append(child.pk)
                matched.add(key)
            else:
                child = create_from_input(
                    spec.model,
                    {**item, spec.fk: parent},
                    field_map=spec.field_map,
                    exclude_fields=spec.exclude_fields,
                    m2m=child_m2m,
                    children=spec.children,
                ).instance
                created_pks.append(child.pk)
        deleted_pks, unlinked_pks = _remove_orphans(existing_by_key, spec, matched, created)
        deltas.append(
            ChildCollectionChange(
                relation=relation,
                created=tuple(created_pks),
                updated=tuple(updated_pks),
                deleted=tuple(deleted_pks),
                unlinked=tuple(unlinked_pks),
            )
        )
    return tuple(deltas)


def _remove_orphans(
    existing_by_key: dict[Any, Model],
    spec: ChildSpec,
    matched: set[Any],
    created: bool,
) -> tuple[list[Any], list[Any]]:
    """Remove pre-update children not matched by the incoming set (replace mode).

    Iterates the *original* snapshot, never a fresh query, so children created
    in this same call are not mistaken for orphans.
    """
    deleted_pks: list[Any] = []
    unlinked_pks: list[Any] = []
    if created or spec.mode != "replace":
        return (deleted_pks, unlinked_pks)
    nullable = _child_fk_nullable(spec)
    for key, child in existing_by_key.items():
        if key in matched:
            continue
        status, pk = remove_child(child, spec.fk, nullable=nullable)
        (unlinked_pks if status == "unlinked" else deleted_pks).append(pk)
    return (deleted_pks, unlinked_pks)


async def aapply_children(
    parent: Model,
    child_data: dict[str, Any],
    children: Mapping[str, ChildSpec],
    *,
    created: bool,
) -> tuple[ChildCollectionChange, ...]:
    """Async variant of :func:`apply_children`."""
    # Lazy import: genuine recursion cycle — the parent helpers call this, and
    # this calls them again for each child (and grandchild).
    from rest_framework_services.mutations.acreate_from_input import acreate_from_input
    from rest_framework_services.mutations.aupdate_from_input import aupdate_from_input

    deltas: list[ChildCollectionChange] = []
    for relation, spec in children.items():
        items = child_data.get(relation, UNSET)
        if items is UNSET:
            deltas.append(ChildCollectionChange(relation=relation))
            continue
        existing_by_key: dict[Any, Model] = {}
        if not created:
            existing_by_key = {
                getattr(e, spec.match_key): e async for e in getattr(parent, relation).all()
            }
        created_pks: list[Any] = []
        updated_pks: list[Any] = []
        matched: set[Any] = set()
        for item in (coerce_to_dict(i) for i in (items or [])):
            child_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
            key = item.get(spec.match_key)
            if key is not None and key in existing_by_key:
                child = existing_by_key[key]
                await aupdate_from_input(
                    child,
                    item,
                    field_map=spec.field_map,
                    exclude_fields=spec.exclude_fields,
                    m2m=child_m2m,
                    children=spec.children,
                )
                updated_pks.append(child.pk)
                matched.add(key)
            else:
                result = await acreate_from_input(
                    spec.model,
                    {**item, spec.fk: parent},
                    field_map=spec.field_map,
                    exclude_fields=spec.exclude_fields,
                    m2m=child_m2m,
                    children=spec.children,
                )
                created_pks.append(result.instance.pk)
        deleted_pks, unlinked_pks = await _aremove_orphans(existing_by_key, spec, matched, created)
        deltas.append(
            ChildCollectionChange(
                relation=relation,
                created=tuple(created_pks),
                updated=tuple(updated_pks),
                deleted=tuple(deleted_pks),
                unlinked=tuple(unlinked_pks),
            )
        )
    return tuple(deltas)


async def _aremove_orphans(
    existing_by_key: dict[Any, Model],
    spec: ChildSpec,
    matched: set[Any],
    created: bool,
) -> tuple[list[Any], list[Any]]:
    """Async variant of :func:`_remove_orphans`."""
    deleted_pks: list[Any] = []
    unlinked_pks: list[Any] = []
    if created or spec.mode != "replace":
        return (deleted_pks, unlinked_pks)
    nullable = _child_fk_nullable(spec)
    for key, child in existing_by_key.items():
        if key in matched:
            continue
        status, pk = await aremove_child(child, spec.fk, nullable=nullable)
        (unlinked_pks if status == "unlinked" else deleted_pks).append(pk)
    return (deleted_pks, unlinked_pks)


def delete_children(
    parent: Model,
    children: Mapping[str, ChildSpec],
) -> tuple[ChildCollectionChange, ...]:
    """Remove every child in each declared collection (grandchildren first).

    Unlinks nullable-FK children (like ``SET_NULL``) and deletes the rest (like
    ``CASCADE``), recursing through ``spec.children`` so a non-nullable
    grandchild is removed before its parent. Used by the default
    :func:`~rest_framework_services.delete_model` service before it deletes the
    top-level instance.
    """
    deltas: list[ChildCollectionChange] = []
    for relation, spec in children.items():
        nullable = _child_fk_nullable(spec)
        deleted_pks: list[Any] = []
        unlinked_pks: list[Any] = []
        for child in getattr(parent, relation).all():
            if spec.children:
                delete_children(child, spec.children)
            status, pk = remove_child(child, spec.fk, nullable=nullable)
            (unlinked_pks if status == "unlinked" else deleted_pks).append(pk)
        deltas.append(
            ChildCollectionChange(
                relation=relation,
                deleted=tuple(deleted_pks),
                unlinked=tuple(unlinked_pks),
            )
        )
    return tuple(deltas)


async def adelete_children(
    parent: Model,
    children: Mapping[str, ChildSpec],
) -> tuple[ChildCollectionChange, ...]:
    """Async variant of :func:`delete_children`."""
    deltas: list[ChildCollectionChange] = []
    for relation, spec in children.items():
        nullable = _child_fk_nullable(spec)
        deleted_pks: list[Any] = []
        unlinked_pks: list[Any] = []
        async for child in getattr(parent, relation).all():
            if spec.children:
                await adelete_children(child, spec.children)
            status, pk = await aremove_child(child, spec.fk, nullable=nullable)
            (unlinked_pks if status == "unlinked" else deleted_pks).append(pk)
        deltas.append(
            ChildCollectionChange(
                relation=relation,
                deleted=tuple(deleted_pks),
                unlinked=tuple(unlinked_pks),
            )
        )
    return tuple(deltas)
