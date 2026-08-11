"""Internal helpers shared by the mutation functions.

Nothing in this module is exported from the package's public API.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import fields, is_dataclass
from typing import Any

from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.db.models import Model

from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)
from rest_framework_services.services.arun_service import arun_service
from rest_framework_services.services.run_service import run_service
from rest_framework_services.types.child_collection_change import ChildCollectionChange
from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.field_change import FieldChange
from rest_framework_services.types.related_object_change import RelatedObjectChange
from rest_framework_services.types.relation_phase import RelationPhase
from rest_framework_services.types.relation_spec import RelationSpec
from rest_framework_services.types.unset import UNSET
from rest_framework_services.views.utils import resolve_callable_kwargs


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


# --- the relation taxonomy: one map, one ordering rule -------------------

# The phases written *after* the parent's save, in the order they run. The
# ordering is the whole point of the taxonomy, so it is stated once — here —
# and every call site (create and update, sync and async) reads it from this
# tuple instead of restating it. Restating it four times is how the four paths
# would drift.
POST_SAVE_PHASES: tuple[RelationPhase, ...] = (
    RelationPhase.REVERSE,
    RelationPhase.GENERIC,
    RelationPhase.M2M,
)


def merge_relations(
    children: Mapping[str, ChildSpec] | None,
    relations: Mapping[str, RelationSpec] | None,
) -> dict[str, RelationSpec]:
    """Fold the ``children=`` alias and ``relations=`` into one map.

    ``children=`` shipped first and already means "these relations are reverse
    foreign keys", so it stays as the alias for that kind rather than becoming
    a synonym half the callers never update. A name declared in both is
    refused: silently picking one of two specs the author wrote deliberately is
    the failure mode this wave exists to remove.
    """
    merged: dict[str, RelationSpec] = {}
    for keyword, declared in (("children", children), ("relations", relations)):
        for name, spec in (declared or {}).items():
            if name in merged:
                raise ImproperlyConfigured(
                    f"relations[{name!r}] is also declared in children=. A relation is "
                    "written once, so declare it in one map or the other — children= "
                    "is the reverse-FK alias for relations=, not a second pass."
                )
            if not isinstance(spec, RelationSpec):
                raise ImproperlyConfigured(
                    f"{keyword}[{name!r}] is a {type(spec).__name__}, which is not a "
                    "relation spec. Declare the relation with the spec class for its "
                    "kind, so the write order can be read off the class."
                )
            merged[name] = spec
    return merged


def extract_relation_data(
    raw: dict[str, Any],
    relations: Mapping[str, RelationSpec],
) -> dict[str, Any]:
    """Pop each relation key out of ``raw`` and return ``{relation: value}``.

    Removing the keys keeps the nested payloads out of the scalar field set the
    parent's ``create``/``update`` would otherwise try to assign — a forward
    relation's resolved instance is put back afterwards, once there is a row to
    assign. A relation the input omitted entirely maps to ``UNSET`` (left
    untouched by the write); an explicit ``[]`` or ``None`` maps to itself and
    is processed.
    """
    return {name: raw.pop(name, UNSET) for name in relations}


def relations_in_phase(
    relations: Mapping[str, RelationSpec],
    phase: RelationPhase,
) -> tuple[tuple[str, RelationSpec], ...]:
    """The declared relations belonging to ``phase``, in declaration order."""
    return tuple((name, spec) for name, spec in relations.items() if spec.write_phase is phase)


def post_save_relations(
    relations: Mapping[str, RelationSpec],
) -> tuple[tuple[str, RelationSpec], ...]:
    """Every relation written after the parent's ``save()``, in phase order."""
    return tuple(
        pair for phase in POST_SAVE_PHASES for pair in relations_in_phase(relations, phase)
    )


def _fk_nullable(spec: ChildSpec) -> bool:
    """Whether the related row's foreign key to the parent allows ``NULL``."""
    return bool(spec.model._meta.get_field(spec.fk).null)


def _collect_removals(removals: list[tuple[str, Any]]) -> dict[str, tuple[Any, ...]]:
    """Bucket ``(status, pk)`` pairs into the change carrier's removal tuples.

    Three buckets, not two: ``deleted`` and ``unlinked`` are what the loop's
    own rule did, and ``removed`` is what a ``delete_service`` did, which the
    loop cannot classify further without inventing an answer.
    """
    buckets: dict[str, list[Any]] = {"deleted": [], "unlinked": [], "removed": []}
    for status, pk in removals:
        buckets[status].append(pk)
    return {name: tuple(pks) for name, pks in buckets.items()}


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


def _pk_input_names(model: type[Model], field_map: dict[str, str] | None) -> frozenset[str]:
    """Input keys on a nested payload that would land on ``model``'s primary key.

    Both spellings Django accepts (``pk`` and the concrete field's name /
    ``attname``), plus any input key ``field_map`` routes onto one of them.
    """
    targets: set[str] = {"pk", model._meta.pk.name, model._meta.pk.attname}
    mapped: set[str] = {src for src, dest in (field_map or {}).items() if dest in targets}
    return frozenset(targets | mapped)


def _reject_unmatched_reference(
    item: dict[str, Any],
    spec: ChildSpec,
    relation: str,
) -> None:
    """Refuse a nested row that names a primary key this parent does not own.

    A create branch reached with a caller-supplied primary key is not a
    create. ``Model(pk=7, fk=parent).save()`` is an **UPDATE** of row 7, so a
    payload carrying a pk that did not match this parent's collection reaches,
    reassigns and overwrites a row belonging to somebody else -- the parent
    scoping that makes the match safe does not constrain the write that follows
    it.

    Raising rather than stripping the key is deliberate: the caller named a
    specific row, and quietly creating a different one does the opposite of
    what was asked. A non-primary ``match_key`` (a natural key such as an ISBN)
    is untouched, so declaring one still upserts.
    """
    named: dict[str, Any] = {
        key: item[key]
        for key in _pk_input_names(spec.model, spec.field_map)
        if item.get(key) is not None
    }
    if not named:
        return
    raise ServiceValidationError(
        {
            relation: [
                f"references {spec.model.__name__} {sorted(named.values())!r}, which is "
                f"not part of this collection. Send a row that belongs to this parent, "
                f"or omit the identifier to create a new one."
            ]
        }
    )


# --- per-child service slots ---------------------------------------------


def _child_pool(context: Mapping[str, Any] | None, **seeds: Any) -> dict[str, Any]:
    """Merge the opaque caller ``context`` with the loop's own seeds.

    The seeds are applied **last**, so a ``context`` key named ``data`` /
    ``instance`` / ``parent`` cannot outrank the value this loop resolved. That
    is the same guarantee ``strip_reserved_seeds`` gives the dispatcher's pools,
    expressed as precedence rather than as a filter: there the mapping being
    merged is client-routable input and the reserved names have to be dropped
    outright, whereas here the context *is* the dispatcher's authoritative pool
    and its ``user`` / ``request`` are exactly what the nested service is meant
    to receive. Filtering it would delete the feature; ordering it keeps the
    loop's own values authoritative, which is all that was ever at risk.
    """
    return {**(context or {}), **seeds}


def _run_child_service(fn: Callable[..., Any], pool: dict[str, Any]) -> Any:
    """Invoke a per-child service from sync code, opening no savepoint.

    ``atomic=False`` deliberately: the surrounding service's atomic block
    already wraps the whole tree, so a nested service opening its own would buy
    no extra guarantee and cost one savepoint per row.
    """
    return run_service(fn, resolve_callable_kwargs(fn, pool), atomic=False)


async def _arun_child_service(fn: Callable[..., Awaitable[Any]], pool: dict[str, Any]) -> Any:
    """Async variant of :func:`_run_child_service` (the slot must be ``async def``)."""
    return await arun_service(fn, resolve_callable_kwargs(fn, pool), atomic=False)


def _remove_one_child(
    child: Model,
    spec: ChildSpec,
    *,
    parent: Model,
    context: Mapping[str, Any] | None,
    nullable: bool,
) -> tuple[str, Any]:
    """Remove one child through ``delete_service`` when declared, else the rule.

    A declared service owns the row, so the loop can no longer distinguish an
    unlink from a delete — and rather than guess one, it reports ``"removed"``,
    the one thing it does know. The pk is read *before* the call, because a
    service that really deletes leaves ``instance.pk`` cleared behind it.
    """
    if spec.delete_service is None:
        return remove_child(child, spec.fk, nullable=nullable)
    pk = child.pk
    _run_child_service(spec.delete_service, _child_pool(context, instance=child, parent=parent))
    return ("removed", pk)


async def _aremove_one_child(
    child: Model,
    spec: ChildSpec,
    *,
    parent: Model,
    context: Mapping[str, Any] | None,
    nullable: bool,
) -> tuple[str, Any]:
    """Async variant of :func:`_remove_one_child`."""
    if spec.delete_service is None:
        return await aremove_child(child, spec.fk, nullable=nullable)
    pk = child.pk
    await _arun_child_service(
        spec.delete_service, _child_pool(context, instance=child, parent=parent)
    )
    return ("removed", pk)


def apply_relations(
    parent: Model,
    relation_data: dict[str, Any],
    relations: Mapping[str, RelationSpec],
    *,
    created: bool,
    context: Mapping[str, Any] | None = None,
) -> tuple[tuple[ChildCollectionChange, ...], tuple[RelatedObjectChange, ...]]:
    """Write every relation that belongs after the parent's ``save()``.

    The one post-save driver, shared by ``create_from_input`` and
    ``update_from_input``: the ordering comes from :func:`post_save_relations`
    and the per-kind work from the writer for that kind, so neither path can
    grow an order of its own. Returns the collection deltas and the singular
    ones separately, because the two shapes report differently — see
    :class:`~rest_framework_services.ChildCollectionChange` and
    :class:`~rest_framework_services.RelatedObjectChange`.

    ``context`` is the opaque caller pool; it is forwarded verbatim, both into
    the per-row helper call (so a grandchild's service sees the same pool as a
    child's) and into the pool of any service the spec declares. Nothing in
    this driver reads it.
    """
    collections: list[ChildCollectionChange] = []
    # Singular post-save kinds report here, in the second slot of the result.
    singular: list[RelatedObjectChange] = []
    for relation, spec in post_save_relations(relations):
        value = relation_data.get(relation, UNSET)
        if isinstance(spec, ChildSpec):
            collections.append(
                _write_child_collection(
                    parent, value, spec, relation=relation, created=created, context=context
                )
            )
        else:
            raise _unknown_relation_kind(relation, spec)
    return (tuple(collections), tuple(singular))


def _unknown_relation_kind(relation: str, spec: RelationSpec) -> ImproperlyConfigured:
    """The error for a relation spec the driver has no writer for.

    Reachable only through a :class:`RelationSpec` subclass the library did not
    define: the ``write_phase`` says when to write it and nothing says how.
    """
    return ImproperlyConfigured(
        f"relations[{relation!r}]: {type(spec).__name__} is not a relation kind this "
        "library knows how to write. Declare the relation with one of the shipped "
        "spec classes."
    )


def _write_child_collection(
    parent: Model,
    items: Any,
    spec: ChildSpec,
    *,
    relation: str,
    created: bool,
    context: Mapping[str, Any] | None,
) -> ChildCollectionChange:
    """Reconcile one reverse-FK child collection against ``items``.

    Match incoming rows to existing children by the spec's ``match_key``
    (skipped on ``created`` — a fresh parent has none), update matches, create
    the rest, and — in ``replace`` mode — remove orphans via
    :func:`remove_child`. Each child runs back through ``create``/``update`` so
    scalar / m2m / nested semantics compose recursively.
    """
    if items is UNSET:
        return ChildCollectionChange(relation=relation)
    existing_by_key: dict[Any, Model] = (
        {} if created else {getattr(e, spec.match_key): e for e in getattr(parent, relation).all()}
    )
    created_pks: list[Any] = []
    updated_pks: list[Any] = []
    matched: set[Any] = set()
    for item in (coerce_to_dict(i) for i in (items or [])):
        child_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
        key = item.get(spec.match_key)
        if key is not None and key in existing_by_key:
            child = _update_child(
                existing_by_key[key],
                item,
                spec,
                parent=parent,
                context=context,
                m2m=child_m2m,
            )
            updated_pks.append(child.pk)
            matched.add(key)
        else:
            _reject_unmatched_reference(item, spec, relation)
            child = _create_child(item, spec, parent=parent, context=context, m2m=child_m2m)
            created_pks.append(child.pk)
    removals = _remove_orphans(
        existing_by_key, spec, matched, created, parent=parent, context=context
    )
    return ChildCollectionChange(
        relation=relation,
        created=tuple(created_pks),
        updated=tuple(updated_pks),
        **_collect_removals(removals),
    )


def _create_child(
    item: dict[str, Any],
    spec: ChildSpec,
    *,
    parent: Model,
    context: Mapping[str, Any] | None,
    m2m: dict[str, Any] | None,
) -> Any:
    """Persist one new child through ``create_service`` when declared, else the helper.

    The ``fk`` is written into ``data`` either way: linking the row to its
    parent is the spec's job, not the service's.
    """
    # Lazy import: genuine recursion cycle — the parent helpers call this loop,
    # and it calls them again for each child (and grandchild).
    from rest_framework_services.mutations.create_from_input import create_from_input

    data: dict[str, Any] = {**item, spec.fk: parent}
    if spec.create_service is not None:
        return _run_child_service(
            spec.create_service, _child_pool(context, data=data, parent=parent)
        )
    return create_from_input(
        spec.model,
        data,
        field_map=spec.field_map,
        exclude_fields=spec.exclude_fields,
        m2m=m2m,
        children=spec.children,
        relations=spec.relations,
        context=context,
    ).instance


async def _acreate_child(
    item: dict[str, Any],
    spec: ChildSpec,
    *,
    parent: Model,
    context: Mapping[str, Any] | None,
    m2m: dict[str, Any] | None,
) -> Any:
    """Async variant of :func:`_create_child`."""
    # Lazy import: genuine recursion cycle — see :func:`_create_child`.
    from rest_framework_services.mutations.acreate_from_input import acreate_from_input

    data: dict[str, Any] = {**item, spec.fk: parent}
    if spec.create_service is not None:
        return await _arun_child_service(
            spec.create_service, _child_pool(context, data=data, parent=parent)
        )
    result = await acreate_from_input(
        spec.model,
        data,
        field_map=spec.field_map,
        exclude_fields=spec.exclude_fields,
        m2m=m2m,
        children=spec.children,
        relations=spec.relations,
        context=context,
    )
    return result.instance


def _update_child(
    child: Model,
    item: dict[str, Any],
    spec: ChildSpec,
    *,
    parent: Model,
    context: Mapping[str, Any] | None,
    m2m: dict[str, Any] | None,
) -> Any:
    """Persist one matched child through ``update_service`` when declared.

    A service returning ``None`` means "use the in-memory instance" — the
    framework's existing update convention, honoured here too.
    """
    # Lazy import: genuine recursion cycle — see :func:`_create_child`.
    from rest_framework_services.mutations.update_from_input import update_from_input

    if spec.update_service is not None:
        returned = _run_child_service(
            spec.update_service,
            _child_pool(context, data=item, instance=child, parent=parent),
        )
        return child if returned is None else returned
    update_from_input(
        child,
        item,
        field_map=spec.field_map,
        exclude_fields=spec.exclude_fields,
        m2m=m2m,
        children=spec.children,
        relations=spec.relations,
        context=context,
    )
    return child


async def _aupdate_child(
    child: Model,
    item: dict[str, Any],
    spec: ChildSpec,
    *,
    parent: Model,
    context: Mapping[str, Any] | None,
    m2m: dict[str, Any] | None,
) -> Any:
    """Async variant of :func:`_update_child`."""
    # Lazy import: genuine recursion cycle — see :func:`_create_child`.
    from rest_framework_services.mutations.aupdate_from_input import aupdate_from_input

    if spec.update_service is not None:
        returned = await _arun_child_service(
            spec.update_service,
            _child_pool(context, data=item, instance=child, parent=parent),
        )
        return child if returned is None else returned
    await aupdate_from_input(
        child,
        item,
        field_map=spec.field_map,
        exclude_fields=spec.exclude_fields,
        m2m=m2m,
        children=spec.children,
        relations=spec.relations,
        context=context,
    )
    return child


def _remove_orphans(
    existing_by_key: dict[Any, Model],
    spec: ChildSpec,
    matched: set[Any],
    created: bool,
    *,
    parent: Model,
    context: Mapping[str, Any] | None,
) -> list[tuple[str, Any]]:
    """Remove pre-update children not matched by the incoming set (replace mode).

    Iterates the *original* snapshot, never a fresh query, so children created
    in this same call are not mistaken for orphans. Returns the
    ``(status, pk)`` pairs :func:`_collect_removals` buckets.
    """
    removals: list[tuple[str, Any]] = []
    if created or spec.mode != "replace":
        return removals
    nullable = _fk_nullable(spec)
    for key, child in existing_by_key.items():
        if key in matched:
            continue
        removals.append(
            _remove_one_child(child, spec, parent=parent, context=context, nullable=nullable)
        )
    return removals


async def aapply_relations(
    parent: Model,
    relation_data: dict[str, Any],
    relations: Mapping[str, RelationSpec],
    *,
    created: bool,
    context: Mapping[str, Any] | None = None,
) -> tuple[tuple[ChildCollectionChange, ...], tuple[RelatedObjectChange, ...]]:
    """Async variant of :func:`apply_relations` — same ordering, awaited."""
    collections: list[ChildCollectionChange] = []
    singular: list[RelatedObjectChange] = []
    for relation, spec in post_save_relations(relations):
        value = relation_data.get(relation, UNSET)
        if isinstance(spec, ChildSpec):
            collections.append(
                await _awrite_child_collection(
                    parent, value, spec, relation=relation, created=created, context=context
                )
            )
        else:
            raise _unknown_relation_kind(relation, spec)
    return (tuple(collections), tuple(singular))


async def _awrite_child_collection(
    parent: Model,
    items: Any,
    spec: ChildSpec,
    *,
    relation: str,
    created: bool,
    context: Mapping[str, Any] | None,
) -> ChildCollectionChange:
    """Async variant of :func:`_write_child_collection`."""
    if items is UNSET:
        return ChildCollectionChange(relation=relation)
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
            child = await _aupdate_child(
                existing_by_key[key],
                item,
                spec,
                parent=parent,
                context=context,
                m2m=child_m2m,
            )
            updated_pks.append(child.pk)
            matched.add(key)
        else:
            _reject_unmatched_reference(item, spec, relation)
            child = await _acreate_child(item, spec, parent=parent, context=context, m2m=child_m2m)
            created_pks.append(child.pk)
    removals = await _aremove_orphans(
        existing_by_key, spec, matched, created, parent=parent, context=context
    )
    return ChildCollectionChange(
        relation=relation,
        created=tuple(created_pks),
        updated=tuple(updated_pks),
        **_collect_removals(removals),
    )


async def _aremove_orphans(
    existing_by_key: dict[Any, Model],
    spec: ChildSpec,
    matched: set[Any],
    created: bool,
    *,
    parent: Model,
    context: Mapping[str, Any] | None,
) -> list[tuple[str, Any]]:
    """Async variant of :func:`_remove_orphans`."""
    removals: list[tuple[str, Any]] = []
    if created or spec.mode != "replace":
        return removals
    nullable = _fk_nullable(spec)
    for key, child in existing_by_key.items():
        if key in matched:
            continue
        removals.append(
            await _aremove_one_child(child, spec, parent=parent, context=context, nullable=nullable)
        )
    return removals


def delete_children(
    parent: Model,
    children: Mapping[str, ChildSpec],
    *,
    context: Mapping[str, Any] | None = None,
) -> tuple[ChildCollectionChange, ...]:
    """Remove every child in each declared collection (grandchildren first).

    Unlinks nullable-FK children (like ``SET_NULL``) and deletes the rest (like
    ``CASCADE``), recursing through ``spec.children`` so a non-nullable
    grandchild is removed before its parent. Used by the default
    :func:`~rest_framework_services.delete_model` service before it deletes the
    top-level instance.

    ``context`` is the opaque caller pool, forwarded down the tree and into the
    pool of any service the spec declares; this loop never reads it.
    """
    deltas: list[ChildCollectionChange] = []
    for relation, spec in children.items():
        nullable = _fk_nullable(spec)
        removals: list[tuple[str, Any]] = []
        for child in getattr(parent, relation).all():
            if spec.children:
                delete_children(child, spec.children, context=context)
            removals.append(
                _remove_one_child(child, spec, parent=parent, context=context, nullable=nullable)
            )
        deltas.append(ChildCollectionChange(relation=relation, **_collect_removals(removals)))
    return tuple(deltas)


async def adelete_children(
    parent: Model,
    children: Mapping[str, ChildSpec],
    *,
    context: Mapping[str, Any] | None = None,
) -> tuple[ChildCollectionChange, ...]:
    """Async variant of :func:`delete_children`."""
    deltas: list[ChildCollectionChange] = []
    for relation, spec in children.items():
        nullable = _fk_nullable(spec)
        removals: list[tuple[str, Any]] = []
        async for child in getattr(parent, relation).all():
            if spec.children:
                await adelete_children(child, spec.children, context=context)
            removals.append(
                await _aremove_one_child(
                    child, spec, parent=parent, context=context, nullable=nullable
                )
            )
        deltas.append(ChildCollectionChange(relation=relation, **_collect_removals(removals)))
    return tuple(deltas)
