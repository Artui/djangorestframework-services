"""Internal helpers shared by the mutation functions.

Nothing in this module is exported from the package's public API.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import fields, is_dataclass
from typing import Any

from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.db.models import Model

from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.services.arun_service import arun_service
from rest_framework_services.services.run_service import run_service
from rest_framework_services.types.child_collection_change import ChildCollectionChange
from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.field_change import FieldChange
from rest_framework_services.types.forward_relation_spec import ForwardRelationSpec
from rest_framework_services.types.many_to_many_spec import ManyToManySpec
from rest_framework_services.types.related_object_change import RelatedObjectChange
from rest_framework_services.types.relation_phase import RelationPhase
from rest_framework_services.types.relation_spec import RelationSpec
from rest_framework_services.types.reverse_one_to_one_spec import ReverseOneToOneSpec
from rest_framework_services.types.unset import UNSET
from rest_framework_services.views.utils import resolve_callable_kwargs

# The kinds whose row the parent owns through a foreign key: the loop links it
# on the way in and disposes of it on the way out. "Child" throughout this
# module means such a row -- a member of a collection, or the single row of a
# reverse one-to-one.
_OwnedRowSpec = ChildSpec | ReverseOneToOneSpec
# The kinds pointing at a row the parent does *not* own -- shared with whoever
# else points at it -- so there is no manager to match within and ``scope=`` is
# what says which rows this caller may write.
_ScopedSpec = ForwardRelationSpec | ManyToManySpec
# Every kind whose row the mutation helpers write, owned or merely pointed at.
_RowSpec = ChildSpec | ReverseOneToOneSpec | ForwardRelationSpec | ManyToManySpec


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


def reject_m2m_overlap(
    m2m: Mapping[str, Any] | None,
    relations: Mapping[str, RelationSpec],
) -> None:
    """Refuse a relation written by ``m2m=`` and by a relation spec at once.

    The two keyword arguments do different jobs and both stay: ``m2m=`` assigns
    rows that already exist (pks or instances), a
    :class:`~rest_framework_services.ManyToManySpec` writes the rows from the
    payload and then links them. What they cannot do is share a relation — they
    would both write it, in an order neither caller chose, and only the second
    would survive. So the overlap is refused where the other spec
    contradictions are, rather than resolved by a precedence rule nobody would
    remember.
    """
    for name in m2m or {}:
        if name in relations:
            raise ImproperlyConfigured(
                f"{name!r} is declared both in m2m= and as a relation. A relation is "
                "written once: m2m= assigns rows that already exist, a relation spec "
                "writes the rows from the payload and links them. Declaring both writes "
                "it twice and keeps whichever ran last. Assign it or write it, not both."
            )


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


def _fk_nullable(spec: _OwnedRowSpec) -> bool:
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
    spec: _RowSpec,
    relation: str,
) -> None:
    """Refuse a nested row that names a primary key nothing matched.

    A create branch reached with a caller-supplied primary key is not a
    create. ``Model(pk=7, ...).save()`` is an **UPDATE** of row 7, so a payload
    carrying a pk the matching step did not resolve reaches, reassigns and
    overwrites a row belonging to somebody else -- the scoping that makes the
    *match* safe does not constrain the write that follows it.

    Every kind creates rows through :func:`_create_row`, so the check lives
    there rather than in one loop: the hazard is the primary key reaching a
    create, and which relation kind carried it there changes nothing. A child
    collection matches within the parent's manager, a forward target and a
    many-to-many target within ``scope=``, a reverse one-to-one through the
    parent's own foreign key and a generic relation through the content
    type -- and a payload can slip a pk past *any* of them, by naming a row the
    match did not cover or by declaring a natural ``match_key`` and putting the
    pk beside it.

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
                f"references {spec.model.__name__} {sorted(named.values())!r}, which this "
                f"write did not match. Saving a new row under a primary key would "
                f"overwrite the row that holds it, so it is refused. Send a row this "
                f"relation may match, or omit the identifier to create a new one."
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
    spec: _OwnedRowSpec,
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
    spec: _OwnedRowSpec,
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


# --- the forward phase: written before the parent exists ------------------


def apply_forward_relations(
    relation_data: dict[str, Any],
    relations: Mapping[str, RelationSpec],
    *,
    context: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], tuple[RelatedObjectChange, ...]]:
    """Resolve every forward relation and return it as plain field assignments.

    The one pre-save driver, shared by create and update. It writes the target
    rows and hands back ``{field_name: instance_or_None}`` for the caller to
    fold into the values it was going to assign anyway — which is the whole
    trick of the forward kind: by the time the parent is built or diffed, the
    relation is an ordinary column value, so ``diff_attrs`` reports it and the
    minimal ``update_fields`` save persists it with nothing added for the
    occasion.
    """
    assignments: dict[str, Any] = {}
    changes: list[RelatedObjectChange] = []
    for relation, spec in relations_in_phase(relations, RelationPhase.FORWARD):
        if not isinstance(spec, ForwardRelationSpec):
            raise _unknown_relation_kind(relation, spec)
        value = relation_data.get(relation, UNSET)
        if value is UNSET:
            changes.append(RelatedObjectChange(relation=relation))
            continue
        target, change = _write_forward_relation(value, spec, relation=relation, context=context)
        assignments[relation] = target
        changes.append(change)
    return (assignments, tuple(changes))


async def aapply_forward_relations(
    relation_data: dict[str, Any],
    relations: Mapping[str, RelationSpec],
    *,
    context: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], tuple[RelatedObjectChange, ...]]:
    """Async variant of :func:`apply_forward_relations`."""
    assignments: dict[str, Any] = {}
    changes: list[RelatedObjectChange] = []
    for relation, spec in relations_in_phase(relations, RelationPhase.FORWARD):
        if not isinstance(spec, ForwardRelationSpec):
            raise _unknown_relation_kind(relation, spec)
        value = relation_data.get(relation, UNSET)
        if value is UNSET:
            changes.append(RelatedObjectChange(relation=relation))
            continue
        target, change = await _awrite_forward_relation(
            value, spec, relation=relation, context=context
        )
        assignments[relation] = target
        changes.append(change)
    return (assignments, tuple(changes))


def _write_forward_relation(
    value: Any,
    spec: ForwardRelationSpec,
    *,
    relation: str,
    context: Mapping[str, Any] | None,
) -> tuple[Any, RelatedObjectChange]:
    """Write (or clear) one forward relation and return what to assign.

    ``None`` clears the parent's column and stops there: the row the column
    pointed at is not the parent's to remove.
    """
    if value is None:
        return (None, RelatedObjectChange(relation=relation, outcome="cleared"))
    item = coerce_to_dict(value)
    row_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
    target = _match_scoped_target(item, spec, relation=relation, context=context)
    if target is None:
        row = _create_row(item, spec, relation=relation, seeds={}, context=context, m2m=row_m2m)
        return (row, RelatedObjectChange(relation=relation, outcome="created", pk=row.pk))
    row = _update_row(target, item, spec, seeds={}, context=context, m2m=row_m2m)
    return (row, RelatedObjectChange(relation=relation, outcome="updated", pk=row.pk))


async def _awrite_forward_relation(
    value: Any,
    spec: ForwardRelationSpec,
    *,
    relation: str,
    context: Mapping[str, Any] | None,
) -> tuple[Any, RelatedObjectChange]:
    """Async variant of :func:`_write_forward_relation`."""
    if value is None:
        return (None, RelatedObjectChange(relation=relation, outcome="cleared"))
    item = coerce_to_dict(value)
    row_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
    target = await _amatch_scoped_target(item, spec, relation=relation, context=context)
    if target is None:
        row = await _acreate_row(
            item, spec, relation=relation, seeds={}, context=context, m2m=row_m2m
        )
        return (row, RelatedObjectChange(relation=relation, outcome="created", pk=row.pk))
    row = await _aupdate_row(target, item, spec, seeds={}, context=context, m2m=row_m2m)
    return (row, RelatedObjectChange(relation=relation, outcome="updated", pk=row.pk))


def _resolve_scope(
    item: dict[str, Any],
    spec: _ScopedSpec,
    *,
    relation: str,
    context: Mapping[str, Any] | None,
) -> tuple[Any, Any] | None:
    """Return ``(queryset, key)`` to match on, or ``None`` for "create it".

    ``None`` covers the two create cases: a payload with no match key at all,
    and a scoped spec is never asked about one. An **unscoped** spec that is
    handed a match key is the third case, and it raises — see the module's
    ``ImproperlyConfigured`` message for why that is a misconfiguration rather
    than a client error.

    Shared by the two kinds whose target the parent does not own — a forward
    foreign key and a many-to-many. Both point at a row that may be shared with
    anybody, which is exactly why neither has a manager to match within and why
    both need ``scope=`` before they may match at all.
    """
    key = item.get(spec.match_key)
    if key is None:
        return None
    if spec.scope is None:
        raise ImproperlyConfigured(
            f"relations[{relation!r}]: the incoming row carries a {spec.match_key!r} but the "
            f"spec declares no scope=, which makes it create-only. A {type(spec).__name__} "
            "target is not reached through a manager the parent owns, so matching one by key "
            "unscoped would let any caller write any row of that model by guessing a key. "
            "Declare scope= — a queryset, or a callable resolved from the caller pool — "
            "naming the rows this caller may write."
        )
    # ``Any`` because the two accepted shapes are a queryset and a callable
    # returning one, and the branch that tells them apart is `callable()`.
    scope: Any = spec.scope
    queryset: Any = (
        scope(**resolve_callable_kwargs(scope, dict(context or {}))) if callable(scope) else scope
    )
    return (queryset, key)


def _scoped_match_miss(
    relation: str,
    spec: _ScopedSpec,
    key: Any,
) -> ServiceValidationError:
    """The error for a match key that names no row this caller may write.

    Not a create. A ``match_key`` on an unowned target *identifies* a row —
    "point at this one", "link this one" — so there is nothing sensible to
    create in its place, and creating one anyway is actively unsafe: the
    payload carries the key, so a ``pk`` that named an out-of-scope row would
    be written straight back onto that row by ``Model.save()``, reaching
    exactly the row the scope existed to protect. Unlike the unscoped case, the
    remedy here is the client's — send a key you own, or none — so this is a
    validation error and not a misconfiguration.
    """
    return ServiceValidationError(
        {
            relation: [
                f"No {spec.model.__name__} with {spec.match_key}={key!r} is available to "
                "write: it does not exist, or it is outside the scope this relation may "
                "write. Omit the key to create a new one."
            ]
        }
    )


def _match_scoped_target(
    item: dict[str, Any],
    spec: _ScopedSpec,
    *,
    relation: str,
    context: Mapping[str, Any] | None,
) -> Any:
    """The in-scope row this payload updates, or ``None`` to create one.

    ``None`` means the payload carried no match key at all. A key that matches
    nothing in scope raises rather than falling through to a create.
    """
    resolved = _resolve_scope(item, spec, relation=relation, context=context)
    if resolved is None:
        return None
    queryset, key = resolved
    target = queryset.filter(**{spec.match_key: key}).first()
    if target is None:
        raise _scoped_match_miss(relation, spec, key)
    return target


async def _amatch_scoped_target(
    item: dict[str, Any],
    spec: _ScopedSpec,
    *,
    relation: str,
    context: Mapping[str, Any] | None,
) -> Any:
    """Async variant of :func:`_match_scoped_target`."""
    resolved = _resolve_scope(item, spec, relation=relation, context=context)
    if resolved is None:
        return None
    queryset, key = resolved
    target = await queryset.filter(**{spec.match_key: key}).afirst()
    if target is None:
        raise _scoped_match_miss(relation, spec, key)
    return target


# --- the post-save phases ------------------------------------------------


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
        elif isinstance(spec, ManyToManySpec):
            collections.append(
                _write_m2m_relation(
                    parent, value, spec, relation=relation, created=created, context=context
                )
            )
        elif isinstance(spec, ReverseOneToOneSpec):
            singular.append(
                _write_reverse_one_to_one(
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


def _write_reverse_one_to_one(
    parent: Model,
    value: Any,
    spec: ReverseOneToOneSpec,
    *,
    relation: str,
    created: bool,
    context: Mapping[str, Any] | None,
) -> RelatedObjectChange:
    """Write the parent's single reverse one-to-one row.

    The children loop with the collection taken out: there is at most one row
    and the relation itself is the match, so nothing is matched by key and
    nothing is scoped — the row is reached through the parent's own foreign
    key or it does not exist. ``None`` removes it by the orphan rule (unlink a
    nullable ``fk``, delete otherwise, or hand it to ``delete_service``).

    The existing row is fetched by querying the ``fk`` rather than through the
    reverse accessor: the accessor caches, raises its own ``DoesNotExist``, and
    has no async form, and one query answers all three the same way on both
    paths.
    """
    if value is UNSET:
        return RelatedObjectChange(relation=relation)
    existing = None if created else spec.model.objects.filter(**{spec.fk: parent}).first()
    if value is None:
        if existing is None:
            return RelatedObjectChange(relation=relation)
        status, pk = _remove_one_child(
            existing, spec, parent=parent, context=context, nullable=_fk_nullable(spec)
        )
        return RelatedObjectChange(relation=relation, outcome=status, pk=pk)
    item = coerce_to_dict(value)
    row_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
    if existing is None:
        row = _create_row(
            {**item, spec.fk: parent},
            spec,
            relation=relation,
            seeds={"parent": parent},
            context=context,
            m2m=row_m2m,
        )
        return RelatedObjectChange(relation=relation, outcome="created", pk=row.pk)
    row = _update_row(existing, item, spec, seeds={"parent": parent}, context=context, m2m=row_m2m)
    return RelatedObjectChange(relation=relation, outcome="updated", pk=row.pk)


async def _awrite_reverse_one_to_one(
    parent: Model,
    value: Any,
    spec: ReverseOneToOneSpec,
    *,
    relation: str,
    created: bool,
    context: Mapping[str, Any] | None,
) -> RelatedObjectChange:
    """Async variant of :func:`_write_reverse_one_to_one`."""
    if value is UNSET:
        return RelatedObjectChange(relation=relation)
    existing = None if created else await spec.model.objects.filter(**{spec.fk: parent}).afirst()
    if value is None:
        if existing is None:
            return RelatedObjectChange(relation=relation)
        status, pk = await _aremove_one_child(
            existing, spec, parent=parent, context=context, nullable=_fk_nullable(spec)
        )
        return RelatedObjectChange(relation=relation, outcome=status, pk=pk)
    item = coerce_to_dict(value)
    row_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
    if existing is None:
        row = await _acreate_row(
            {**item, spec.fk: parent},
            spec,
            relation=relation,
            seeds={"parent": parent},
            context=context,
            m2m=row_m2m,
        )
        return RelatedObjectChange(relation=relation, outcome="created", pk=row.pk)
    row = await _aupdate_row(
        existing, item, spec, seeds={"parent": parent}, context=context, m2m=row_m2m
    )
    return RelatedObjectChange(relation=relation, outcome="updated", pk=row.pk)


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
            child = _update_row(
                existing_by_key[key],
                item,
                spec,
                seeds={"parent": parent},
                context=context,
                m2m=child_m2m,
            )
            updated_pks.append(child.pk)
            matched.add(key)
        else:
            child = _create_row(
                {**item, spec.fk: parent},
                spec,
                relation=relation,
                seeds={"parent": parent},
                context=context,
                m2m=child_m2m,
            )
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


def _write_m2m_relation(
    parent: Model,
    items: Any,
    spec: ManyToManySpec,
    *,
    relation: str,
    created: bool,
    context: Mapping[str, Any] | None,
) -> ChildCollectionChange:
    """Write a many-to-many's target rows, then the membership.

    Two steps in that order, and the order is the kind: every target has to
    exist and hold a primary key before there is anything to link, so the rows
    are written first and the manager is handed the finished list once.

    Matching happens in ``scope=``, never in the parent's current membership —
    the payload names the rows to link, which is precisely the set that is not
    linked yet, so matching against the members would make every new link look
    like a create. The current membership is read for one thing only: naming
    the targets ``"replace"`` drops.
    """
    if items is UNSET:
        return ChildCollectionChange(relation=relation)
    manager: Any = getattr(parent, relation)
    before: list[Any] = [] if created else list(manager.values_list("pk", flat=True))
    created_pks: list[Any] = []
    updated_pks: list[Any] = []
    targets: list[Any] = []
    for item in (coerce_to_dict(i) for i in (items or [])):
        row_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
        match = _match_scoped_target(item, spec, relation=relation, context=context)
        if match is None:
            row = _create_row(
                item,
                spec,
                relation=relation,
                seeds={"parent": parent},
                context=context,
                m2m=row_m2m,
            )
            created_pks.append(row.pk)
        else:
            row = _update_row(
                match, item, spec, seeds={"parent": parent}, context=context, m2m=row_m2m
            )
            updated_pks.append(row.pk)
        targets.append(row)
    if spec.mode == "replace":
        manager.set(targets)
    else:
        manager.add(*targets)
    return ChildCollectionChange(
        relation=relation,
        created=tuple(created_pks),
        updated=tuple(updated_pks),
        unlinked=_m2m_dropped(before, targets, spec),
    )


def _m2m_dropped(
    before: list[Any],
    targets: list[Any],
    spec: ManyToManySpec,
) -> tuple[Any, ...]:
    """The members ``"replace"`` dropped — an unlink, never a delete.

    A many-to-many target is shared by definition, so the only thing the loop
    can remove is the membership. That is why this fills
    :attr:`~rest_framework_services.ChildCollectionChange.unlinked` and why
    ``deleted`` stays empty for this kind whatever ``mode`` says.
    """
    if spec.mode != "replace":
        return ()
    kept: set[Any] = {row.pk for row in targets}
    return tuple(pk for pk in before if pk not in kept)


def _create_row(
    data: dict[str, Any],
    spec: _RowSpec,
    *,
    relation: str,
    seeds: dict[str, Any],
    context: Mapping[str, Any] | None,
    m2m: dict[str, Any] | None,
) -> Any:
    """Persist one new related row: ``create_service`` when declared, else the helper.

    The one create used by every kind, which is why ``data`` and ``seeds``
    arrive already built rather than being derived here. What differs between
    the kinds is exactly those two: a row the parent owns gets its link to the
    parent set and a ``parent`` seed in the pool, and a forward target gets
    neither — it is written before there is a parent to speak of.

    Being the one create is also why the primary-key guard sits here, ahead of
    the ``create_service`` dispatch: no kind can reach a create without passing
    it, and a declared service is not handed the key either — otherwise the
    same defect moves one layer out, into code the library cannot see.
    """
    # Lazy import: genuine recursion cycle — the parent helpers call this loop,
    # and it calls them again for each row (and each of its own relations).
    from rest_framework_services.mutations.create_from_input import create_from_input

    _reject_unmatched_reference(data, spec, relation)
    if spec.create_service is not None:
        return _run_child_service(spec.create_service, _child_pool(context, data=data, **seeds))
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


async def _acreate_row(
    data: dict[str, Any],
    spec: _RowSpec,
    *,
    relation: str,
    seeds: dict[str, Any],
    context: Mapping[str, Any] | None,
    m2m: dict[str, Any] | None,
) -> Any:
    """Async variant of :func:`_create_row`."""
    # Lazy import: genuine recursion cycle — see :func:`_create_row`.
    from rest_framework_services.mutations.acreate_from_input import acreate_from_input

    _reject_unmatched_reference(data, spec, relation)
    if spec.create_service is not None:
        return await _arun_child_service(
            spec.create_service, _child_pool(context, data=data, **seeds)
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


def _update_row(
    instance: Model,
    data: dict[str, Any],
    spec: _RowSpec,
    *,
    seeds: dict[str, Any],
    context: Mapping[str, Any] | None,
    m2m: dict[str, Any] | None,
) -> Any:
    """Persist one matched row through ``update_service`` when declared.

    A service returning ``None`` means "use the in-memory instance" — the
    framework's existing update convention, honoured here too.
    """
    # Lazy import: genuine recursion cycle — see :func:`_create_row`.
    from rest_framework_services.mutations.update_from_input import update_from_input

    if spec.update_service is not None:
        returned = _run_child_service(
            spec.update_service,
            _child_pool(context, data=data, instance=instance, **seeds),
        )
        return instance if returned is None else returned
    update_from_input(
        instance,
        data,
        field_map=spec.field_map,
        exclude_fields=spec.exclude_fields,
        m2m=m2m,
        children=spec.children,
        relations=spec.relations,
        context=context,
    )
    return instance


async def _aupdate_row(
    instance: Model,
    data: dict[str, Any],
    spec: _RowSpec,
    *,
    seeds: dict[str, Any],
    context: Mapping[str, Any] | None,
    m2m: dict[str, Any] | None,
) -> Any:
    """Async variant of :func:`_update_row`."""
    # Lazy import: genuine recursion cycle — see :func:`_create_row`.
    from rest_framework_services.mutations.aupdate_from_input import aupdate_from_input

    if spec.update_service is not None:
        returned = await _arun_child_service(
            spec.update_service,
            _child_pool(context, data=data, instance=instance, **seeds),
        )
        return instance if returned is None else returned
    await aupdate_from_input(
        instance,
        data,
        field_map=spec.field_map,
        exclude_fields=spec.exclude_fields,
        m2m=m2m,
        children=spec.children,
        relations=spec.relations,
        context=context,
    )
    return instance


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
        elif isinstance(spec, ManyToManySpec):
            collections.append(
                await _awrite_m2m_relation(
                    parent, value, spec, relation=relation, created=created, context=context
                )
            )
        elif isinstance(spec, ReverseOneToOneSpec):
            singular.append(
                await _awrite_reverse_one_to_one(
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
            child = await _aupdate_row(
                existing_by_key[key],
                item,
                spec,
                seeds={"parent": parent},
                context=context,
                m2m=child_m2m,
            )
            updated_pks.append(child.pk)
            matched.add(key)
        else:
            child = await _acreate_row(
                {**item, spec.fk: parent},
                spec,
                relation=relation,
                seeds={"parent": parent},
                context=context,
                m2m=child_m2m,
            )
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


async def _awrite_m2m_relation(
    parent: Model,
    items: Any,
    spec: ManyToManySpec,
    *,
    relation: str,
    created: bool,
    context: Mapping[str, Any] | None,
) -> ChildCollectionChange:
    """Async variant of :func:`_write_m2m_relation`."""
    if items is UNSET:
        return ChildCollectionChange(relation=relation)
    manager: Any = getattr(parent, relation)
    before: list[Any] = [] if created else await am2m_current_pks(parent, relation)
    created_pks: list[Any] = []
    updated_pks: list[Any] = []
    targets: list[Any] = []
    for item in (coerce_to_dict(i) for i in (items or [])):
        row_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
        match = await _amatch_scoped_target(item, spec, relation=relation, context=context)
        if match is None:
            row = await _acreate_row(
                item,
                spec,
                relation=relation,
                seeds={"parent": parent},
                context=context,
                m2m=row_m2m,
            )
            created_pks.append(row.pk)
        else:
            row = await _aupdate_row(
                match, item, spec, seeds={"parent": parent}, context=context, m2m=row_m2m
            )
            updated_pks.append(row.pk)
        targets.append(row)
    if spec.mode == "replace":
        await manager.aset(targets)
    else:
        await manager.aadd(*targets)
    return ChildCollectionChange(
        relation=relation,
        created=tuple(created_pks),
        updated=tuple(updated_pks),
        unlinked=_m2m_dropped(before, targets, spec),
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


def _uncascadable_relation_kind(relation: str, spec: RelationSpec) -> ImproperlyConfigured:
    """The error for a relation the delete cascade cannot remove.

    The cascade takes the collections a parent owns and hands back one delta
    per collection. A singular relation neither fits that shape nor needs the
    cascade in the same way — clearing it is an explicit ``None`` on the write
    path — so it is refused here instead of being removed and reported as a
    one-row "collection".
    """
    return ImproperlyConfigured(
        f"children[{relation!r}]: {type(spec).__name__} is not a collection, and the "
        "delete cascade removes collections. Remove a singular relation from the write "
        "path instead, by sending null for it."
    )


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

    Collections only. The cascade reports one
    :class:`~rest_framework_services.ChildCollectionChange` per relation, which
    is not the shape a singular relation reports in, so a singular spec is
    refused here rather than removed and mis-reported.
    """
    deltas: list[ChildCollectionChange] = []
    for relation, spec in children.items():
        if not isinstance(spec, ChildSpec):
            raise _uncascadable_relation_kind(relation, spec)
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
        if not isinstance(spec, ChildSpec):
            raise _uncascadable_relation_kind(relation, spec)
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
