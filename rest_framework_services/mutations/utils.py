"""Internal helpers shared by the mutation functions.

Nothing in this module is exported from the package's public API.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from typing import Any

from asgiref.sync import sync_to_async
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.db.models import Model
from rest_framework.exceptions import ValidationError

from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.services.arun_service import arun_service
from rest_framework_services.services.run_service import run_service
from rest_framework_services.types.child_collection_change import ChildCollectionChange
from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.field_change import FieldChange
from rest_framework_services.types.forward_relation_spec import ForwardRelationSpec
from rest_framework_services.types.generic_relation_spec import GenericRelationSpec
from rest_framework_services.types.many_to_many_spec import ManyToManySpec
from rest_framework_services.types.related_object_change import RelatedObjectChange
from rest_framework_services.types.relation_orphan import RelationOrphan
from rest_framework_services.types.relation_outcome import RelationOutcome
from rest_framework_services.types.relation_phase import RelationPhase
from rest_framework_services.types.relation_spec import RelationSpec
from rest_framework_services.types.reverse_one_to_one_spec import ReverseOneToOneSpec
from rest_framework_services.types.unset import UNSET
from rest_framework_services.views.utils import resolve_callable_kwargs

# The kinds whose row the parent owns through a link stored on that row. "Child"
# throughout this module means such a row; a generic-relation row differs only in
# that its link is two columns rather than one.
_OwnedRowSpec = ChildSpec | ReverseOneToOneSpec | GenericRelationSpec
# The owned kinds holding *many* rows, so they reconcile a collection and have
# orphans; the reverse one-to-one holds one and has the ``None`` case instead.
_CollectionSpec = ChildSpec | GenericRelationSpec
# The kinds pointing at a row the parent does *not* own, so there is no manager
# to match within and ``scope=`` says which rows this caller may write.
_ScopedSpec = ForwardRelationSpec | ManyToManySpec
# Every kind whose row the mutation helpers write, owned or merely pointed at.
_RowSpec = (
    ChildSpec | ReverseOneToOneSpec | ForwardRelationSpec | ManyToManySpec | GenericRelationSpec
)


def coerce_to_dict(data: Any) -> dict[str, Any]:
    """Normalize input ``data`` to a dict mapping field name to value."""
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

    ``exclude_fields`` matches input field names, before mapping.
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
    """Async variant of ``m2m_current_pks``."""
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
    """``(changes, to_apply)`` — everything on create, only differences on update."""
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
    """Async variant of ``m2m_changes``."""
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
    """Build [`FieldChange`][rest_framework_services.types.field_change.FieldChange]
    entries for every assigned create field."""
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

    ``True`` narrows the save to the changed columns, so the ``auto_now`` ones
    have to be added back by hand: Django only refreshes them when they are in
    ``update_fields``.
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

# The phases written *after* the parent's save, in the order they run. Stated
# once, here: every call site (create and update, sync and async) reads it from
# this tuple, so the four paths cannot drift.
POST_SAVE_PHASES: tuple[RelationPhase, ...] = (
    RelationPhase.REVERSE,
    RelationPhase.GENERIC,
    RelationPhase.M2M,
)


def merge_relations(
    children: Mapping[str, ChildSpec] | None,
    relations: Mapping[str, RelationSpec] | None,
) -> dict[str, RelationSpec]:
    """Fold the ``children=`` reverse-FK alias and ``relations=`` into one map."""
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
    """Refuse a relation written by ``m2m=`` and by a relation spec at once."""
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

    Popping keeps nested payloads out of the scalar field set the parent's
    write would otherwise assign. An omitted relation maps to ``UNSET`` and is
    left untouched; an explicit ``[]`` or ``None`` maps to itself.
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


def _content_type_for(instance: Model) -> Any:
    """The ``ContentType`` row for ``instance``'s model.

    The import is function-local because this module is imported while
    ``apps.populate()`` runs, where an eager model import raises
    ``AppRegistryNotReady``; the ``is_installed`` guard turns the absent-app
    case into a message about ``contenttypes``.
    """
    if not apps.is_installed("django.contrib.contenttypes"):
        raise ImproperlyConfigured(
            "A generic relation needs 'django.contrib.contenttypes' in INSTALLED_APPS: "
            "the row is linked to its parent by a content type, and without that app "
            "there is no content type to link it to. Add the app, or link the rows with "
            "a plain foreign key and declare the relation as a ChildSpec."
        )
    from django.contrib.contenttypes.models import ContentType

    return ContentType.objects.get_for_model(instance)


def _link_fields(spec: _OwnedRowSpec) -> tuple[str, ...]:
    """The column(s) on the related row holding its link back to the parent."""
    if isinstance(spec, GenericRelationSpec):
        return (spec.content_type_field, spec.object_id_field)
    return (spec.fk,)


def _link_values(spec: _OwnedRowSpec, parent: Model) -> dict[str, Any]:
    """What to assign on a new related row so it points at ``parent``."""
    if isinstance(spec, GenericRelationSpec):
        return {
            spec.content_type_field: _content_type_for(parent),
            spec.object_id_field: parent.pk,
        }
    return {spec.fk: parent}


async def _alink_values(spec: _OwnedRowSpec, parent: Model) -> dict[str, Any]:
    """Async variant of ``_link_values``.

    Only the generic branch takes a thread hop: ``get_for_model`` has no async
    form on any supported Django, while a foreign key needs no query at all.
    """
    if not isinstance(spec, GenericRelationSpec):
        return {spec.fk: parent}
    return {
        spec.content_type_field: await sync_to_async(_content_type_for, thread_sensitive=True)(
            parent
        ),
        spec.object_id_field: parent.pk,
    }


def _fixed_link_fields(spec: _OwnedRowSpec) -> tuple[str, ...]:
    """The link column(s) that cannot hold ``NULL``, so cannot be blanked."""
    return tuple(
        name for name in _link_fields(spec) if not bool(spec.model._meta.get_field(name).null)
    )


def _link_nullable(spec: _OwnedRowSpec) -> bool:
    """Whether the row's link to the parent can be blanked instead of deleted.

    Every column or none: a generic link is severed only when *both* columns
    can hold ``NULL``, since half a link is a meaningless row state.
    """
    return not _fixed_link_fields(spec)


def _unlinks_orphans(spec: _OwnedRowSpec, *, relation: str) -> bool:
    """Whether a row this relation lets go is unlinked rather than deleted.

    The one place ``orphan`` is read, so the update path and the delete cascade
    dispose of a row the same way. ``UNLINK`` against a link that cannot hold
    ``NULL`` raises here rather than at spec construction: specs are routinely
    built at import time, while ``apps.populate()`` runs and ``_meta`` cannot
    be read at all.
    """
    if spec.orphan == RelationOrphan.DELETE:
        return False
    if spec.orphan == RelationOrphan.AUTO:
        return _link_nullable(spec)
    fixed: tuple[str, ...] = _fixed_link_fields(spec)
    if fixed:
        columns = ", ".join(f"{spec.model.__name__}.{name}" for name in fixed)
        raise ImproperlyConfigured(
            f"relations[{relation!r}]: orphan={RelationOrphan.UNLINK.value!r} asks for the row "
            f"to be kept and its link blanked, but {columns} cannot hold NULL, so there is no "
            "link to blank. Make the column nullable (null=True, and the migration for it), or "
            f"declare orphan={RelationOrphan.DELETE.value!r} to remove the row — dropping "
            "orphan= altogether deletes it too, by deriving the rule from the column."
        )
    return True


def _collect_removals(
    removals: list[tuple[RelationOutcome, Any]],
) -> dict[str, tuple[Any, ...]]:
    """Bucket ``(status, pk)`` pairs into the change carrier's removal tuples.

    ``removed`` is the bucket for a ``delete_service``, whose disposal the loop
    cannot classify further.
    """
    buckets: dict[RelationOutcome, list[Any]] = {
        RelationOutcome.DELETED: [],
        RelationOutcome.UNLINKED: [],
        RelationOutcome.REMOVED: [],
    }
    for status, pk in removals:
        buckets[status].append(pk)
    return {outcome.value: tuple(pks) for outcome, pks in buckets.items()}


def remove_child(
    child: Model, link: tuple[str, ...], *, unlink: bool
) -> tuple[RelationOutcome, Any]:
    """Detach (``SET_NULL``) or delete (``CASCADE``) ``child``, as ``unlink`` says.

    Handed the answer by ``_unlinks_orphans`` rather than deriving one.
    Every column of ``link`` is blanked together, and the pk is captured
    *before* the delete because Django clears ``instance.pk``.
    """
    pk = child.pk
    if unlink:
        for name in link:
            setattr(child, name, None)
        child.save(update_fields=list(link))
        return (RelationOutcome.UNLINKED, pk)
    child.delete()
    return (RelationOutcome.DELETED, pk)


async def aremove_child(
    child: Model, link: tuple[str, ...], *, unlink: bool
) -> tuple[RelationOutcome, Any]:
    """Async variant of ``remove_child``."""
    pk = child.pk
    if unlink:
        for name in link:
            setattr(child, name, None)
        await child.asave(update_fields=list(link))
        return (RelationOutcome.UNLINKED, pk)
    await child.adelete()
    return (RelationOutcome.DELETED, pk)


def _pk_input_names(model: type[Model], field_map: dict[str, str] | None) -> frozenset[str]:
    """Input keys landing on ``model``'s pk — every spelling, plus ``field_map``."""
    targets: set[str] = {"pk", model._meta.pk.name, model._meta.pk.attname}
    mapped: set[str] = {src for src, dest in (field_map or {}).items() if dest in targets}
    return frozenset(targets | mapped)


def _omitted(value: Any) -> bool:
    """Whether a nested row supplied this key at all.

    Two spellings mean "not supplied" and the row writers have to read both:
    ``None``, which a mapping uses, and ``UNSET``, which is what a partial-input
    dataclass carries for a field its caller left alone. The sentinel is not a
    third state to reason about -- ``filter_input`` already drops it before
    anything is assigned, so the only places it can be mistaken for a value are
    the reads that happen *before* that, on the way to deciding whether there is
    a row to match.
    """
    return value is None or value is UNSET


def _reject_unmatched_reference(
    item: dict[str, Any],
    spec: _RowSpec,
    relation: str,
) -> None:
    """Refuse a nested row that names a primary key nothing matched.

    ``Model(pk=7, ...).save()`` is an **UPDATE** of row 7, so a payload
    carrying a pk the matching step did not resolve would reach and overwrite a
    row belonging to somebody else: the scoping that makes the *match* safe
    does not constrain the write that follows it. A payload can slip a pk past
    any kind's matching step, which is why the check sits in
    ``_create_row``, the one create every kind goes through.

    Refused rather than stripped, because quietly creating a different row does
    the opposite of what was asked. A non-primary ``match_key`` is untouched,
    so declaring one still upserts.
    """
    named: dict[str, Any] = {
        key: item[key]
        for key in _pk_input_names(spec.model, spec.field_map)
        if not _omitted(item.get(key))
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


# --- where a row's error lands -------------------------------------------


@dataclass(frozen=True)
class _RowPath:
    """Where one row sits in the incoming payload, so its error can name it.

    A relation holding a single row has no ``index`` and no ``length``.
    """

    relation: str
    index: int | None = None
    length: int = 0

    def namespace(self, detail: Any) -> dict[str, Any]:
        """Put ``detail`` under the relation name, at this row's position.

        The shape is DRF's ``ListSerializer`` — a list as long as the incoming
        one, empty dicts against the other rows. ``detail`` passes through
        untouched: a service may raise a string or a list rather than a field
        map, and reshaping either would invent a field name it never named.
        """
        if self.index is None:
            return {self.relation: detail}
        aligned: list[Any] = [{} for _ in range(self.length)]
        aligned[self.index] = detail
        return {self.relation: aligned}


# The two errors a row's write can fail with. A service reaches for whichever
# one it knows, the library's own or DRF's, so both are namespaced on the way
# out rather than one being blessed.
_ROW_WRITE_ERRORS = (ServiceValidationError, ValidationError)


def _namespaced_row_error(
    exc: ServiceValidationError | ValidationError,
    path: _RowPath,
) -> ServiceValidationError | ValidationError:
    """The same error, with the relation (and row) that carried it named.

    The exception class is preserved: a service that reached for DRF's error
    chose its status mapping with it.
    """
    detail: dict[str, Any] = path.namespace(exc.detail)
    if isinstance(exc, ServiceValidationError):
        return ServiceValidationError(detail)
    return ValidationError(detail)


# --- per-child service slots ---------------------------------------------


def _child_pool(context: Mapping[str, Any] | None, **seeds: Any) -> dict[str, Any]:
    """Merge the opaque caller ``context`` with the loop's own seeds.

    The seeds are applied **last**, so a ``context`` key named ``data`` /
    ``instance`` / ``parent`` cannot outrank the value this loop resolved. This
    is the ``strip_reserved_seeds`` guarantee as precedence rather than as a
    filter: the context is the dispatcher's authoritative pool here, so
    filtering its ``user`` / ``request`` out would delete the feature.
    """
    return {**(context or {}), **seeds}


def _run_child_service(fn: Callable[..., Any], pool: dict[str, Any]) -> Any:
    """Invoke a per-child service from sync code, opening no savepoint.

    ``atomic=False`` deliberately: the surrounding service's block already
    wraps the whole tree, so a nested one would cost a savepoint per row and
    guarantee nothing extra.
    """
    return run_service(fn, resolve_callable_kwargs(fn, pool), atomic=False)


async def _arun_child_service(fn: Callable[..., Awaitable[Any]], pool: dict[str, Any]) -> Any:
    """Async variant of ``_run_child_service`` (the slot must be ``async def``)."""
    return await arun_service(fn, resolve_callable_kwargs(fn, pool), atomic=False)


def _remove_one_child(
    child: Model,
    spec: _OwnedRowSpec,
    *,
    parent: Model,
    context: Mapping[str, Any] | None,
    unlink: bool,
) -> tuple[RelationOutcome, Any]:
    """Remove one child through ``delete_service`` when declared, else the rule.

    A declared service owns the row, so the loop reports ``"removed"`` rather
    than guessing. The pk is read *before* the call: a service that really
    deletes leaves ``instance.pk`` cleared behind it.
    """
    if spec.delete_service is None:
        return remove_child(child, _link_fields(spec), unlink=unlink)
    pk = child.pk
    _run_child_service(spec.delete_service, _child_pool(context, instance=child, parent=parent))
    return (RelationOutcome.REMOVED, pk)


async def _aremove_one_child(
    child: Model,
    spec: _OwnedRowSpec,
    *,
    parent: Model,
    context: Mapping[str, Any] | None,
    unlink: bool,
) -> tuple[RelationOutcome, Any]:
    """Async variant of ``_remove_one_child``."""
    if spec.delete_service is None:
        return await aremove_child(child, _link_fields(spec), unlink=unlink)
    pk = child.pk
    await _arun_child_service(
        spec.delete_service, _child_pool(context, instance=child, parent=parent)
    )
    return (RelationOutcome.REMOVED, pk)


# --- the forward phase: written before the parent exists ------------------


def apply_forward_relations(
    relation_data: dict[str, Any],
    relations: Mapping[str, RelationSpec],
    *,
    context: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], tuple[RelatedObjectChange, ...]]:
    """Resolve every forward relation and return it as plain field assignments.

    The one pre-save driver, shared by create and update. Handing back
    ``{field_name: instance_or_None}`` is what lets ``diff_attrs`` report the
    relation and the minimal ``update_fields`` save persist it, with nothing
    added for the occasion.
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
    """Async variant of ``apply_forward_relations``."""
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


def sync_relation_cache(parent: Model, relation: str, row: Any) -> None:
    """Make ``parent``'s cached related object agree with what the write left.

    The two singular kinds are matched by **re-querying** — a forward target
    inside ``scope=``, a reverse one-to-one found through its foreign key — so
    the row that gets saved is a different Python object from the one the parent
    has cached, and nothing else invalidates that cache. A caller who read the
    relation before the write then reads pre-write values back off the instance
    the helper returns, which is what a response serializer renders. Reading it
    first is the ordinary shape: a validator reaching through the relation, a
    before/after comparison, a ``scope=`` callable.

    The diff does not catch the forward case either. Two rows sharing a primary
    key are equal, so the column is correctly left alone — and the stale object
    is left with it.

    ``row`` is ``None`` where the write cleared or removed the relation, and that
    case *drops* the cached entry rather than assigning ``None`` through the
    descriptor: assigning would also blank the removed row's own link in memory,
    which a ``delete_service`` that deliberately kept the row linked never asked
    for. Dropping it lets the next read say what the database says. The entry is
    keyed by the relation name for both kinds — a forward field caches under its
    own name, a reverse one-to-one under its accessor — which is the name the
    spec is declared with either way.

    Assigning is the descriptor's own work, so this costs no query, and it stays
    narrower than the blanket ``refresh_from_db()`` it removes the need for: only
    a relation this write resolved is touched, and only in memory.
    """
    if row is None:
        parent._state.fields_cache.pop(relation, None)
        return
    setattr(parent, relation, row)


def _write_forward_relation(
    value: Any,
    spec: ForwardRelationSpec,
    *,
    relation: str,
    context: Mapping[str, Any] | None,
) -> tuple[Any, RelatedObjectChange]:
    """Write (or clear) one forward relation and return what to assign.

    ``None`` clears the column and stops: the row it pointed at is not the
    parent's to remove.
    """
    if value is None:
        return (None, RelatedObjectChange(relation=relation, outcome=RelationOutcome.CLEARED))
    item = coerce_to_dict(value)
    row_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
    path = _RowPath(relation)
    target = _match_scoped_target(item, spec, relation=relation, context=context)
    if target is None:
        row = _create_row(item, spec, path=path, seeds={}, context=context, m2m=row_m2m)
        return (
            row,
            RelatedObjectChange(relation=relation, outcome=RelationOutcome.CREATED, pk=row.pk),
        )
    row = _update_row(target, item, spec, path=path, seeds={}, context=context, m2m=row_m2m)
    return (row, RelatedObjectChange(relation=relation, outcome=RelationOutcome.UPDATED, pk=row.pk))


async def _awrite_forward_relation(
    value: Any,
    spec: ForwardRelationSpec,
    *,
    relation: str,
    context: Mapping[str, Any] | None,
) -> tuple[Any, RelatedObjectChange]:
    """Async variant of ``_write_forward_relation``."""
    if value is None:
        return (None, RelatedObjectChange(relation=relation, outcome=RelationOutcome.CLEARED))
    item = coerce_to_dict(value)
    row_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
    path = _RowPath(relation)
    target = await _amatch_scoped_target(item, spec, relation=relation, context=context)
    if target is None:
        row = await _acreate_row(item, spec, path=path, seeds={}, context=context, m2m=row_m2m)
        return (
            row,
            RelatedObjectChange(relation=relation, outcome=RelationOutcome.CREATED, pk=row.pk),
        )
    row = await _aupdate_row(target, item, spec, path=path, seeds={}, context=context, m2m=row_m2m)
    return (row, RelatedObjectChange(relation=relation, outcome=RelationOutcome.UPDATED, pk=row.pk))


def _resolve_scope(
    item: dict[str, Any],
    spec: _ScopedSpec,
    *,
    relation: str,
    context: Mapping[str, Any] | None,
) -> tuple[Any, Any] | None:
    """Return ``(queryset, key)`` to match on, or ``None`` for "create it".

    An unscoped spec handed a match key raises: neither of these kinds has a
    manager to match within, so matching by key unscoped would let any caller
    write any row of that model by guessing a key.
    """
    key = item.get(spec.match_key)
    if _omitted(key):
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
    # ``Any``: the two accepted shapes are a queryset and a callable returning
    # one, told apart by ``callable()``.
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

    Never a create: the payload still carries the key, so a ``pk`` naming an
    out-of-scope row would be written straight back onto that row by
    ``Model.save()`` — exactly the row the scope protects.
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

    ``None`` means no match key was sent at all; a key matching nothing in
    scope raises rather than falling through to a create.
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
    """Async variant of ``_match_scoped_target``."""
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

    The one post-save driver, shared by create and update: the ordering comes
    from ``post_save_relations``, so neither path can grow an order of its
    own. ``context`` is the opaque caller pool, forwarded verbatim down the
    tree and into any service a spec declares; this driver never reads it.
    """
    collections: list[ChildCollectionChange] = []
    singular: list[RelatedObjectChange] = []
    for relation, spec in post_save_relations(relations):
        value = relation_data.get(relation, UNSET)
        if isinstance(spec, ChildSpec | GenericRelationSpec):
            collections.append(
                _write_owned_collection(
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
    """The error for a ``RelationSpec`` subclass the library did not define."""
    return ImproperlyConfigured(
        f"relations[{relation!r}]: {type(spec).__name__} is not a relation kind this "
        "library knows how to write or remove. Declare the relation with one of the "
        "shipped spec classes."
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

    The relation itself is the match, so nothing is matched by key and nothing
    is scoped. The existing row is fetched by querying the ``fk`` rather than
    through the reverse accessor, which caches, raises its own
    ``DoesNotExist``, and has no async form. That the fetch bypasses the
    accessor is exactly why the result has to be handed back to it — see
    ``sync_relation_cache``.
    """
    if value is UNSET:
        return RelatedObjectChange(relation=relation)
    existing = None if created else spec.model.objects.filter(**{spec.fk: parent}).first()
    if value is None:
        if existing is None:
            return RelatedObjectChange(relation=relation)
        status, pk = _remove_one_child(
            existing,
            spec,
            parent=parent,
            context=context,
            unlink=_unlinks_orphans(spec, relation=relation),
        )
        sync_relation_cache(parent, relation, None)
        return RelatedObjectChange(relation=relation, outcome=status, pk=pk)
    item = coerce_to_dict(value)
    row_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
    path = _RowPath(relation)
    if existing is None:
        row = _create_row(
            {**item, **_link_values(spec, parent)},
            spec,
            path=path,
            seeds={"parent": parent},
            context=context,
            m2m=row_m2m,
        )
        outcome = RelationOutcome.CREATED
    else:
        row = _update_row(
            existing, item, spec, path=path, seeds={"parent": parent}, context=context, m2m=row_m2m
        )
        outcome = RelationOutcome.UPDATED
    sync_relation_cache(parent, relation, row)
    return RelatedObjectChange(relation=relation, outcome=outcome, pk=row.pk)


async def _awrite_reverse_one_to_one(
    parent: Model,
    value: Any,
    spec: ReverseOneToOneSpec,
    *,
    relation: str,
    created: bool,
    context: Mapping[str, Any] | None,
) -> RelatedObjectChange:
    """Async variant of ``_write_reverse_one_to_one``."""
    if value is UNSET:
        return RelatedObjectChange(relation=relation)
    existing = None if created else await spec.model.objects.filter(**{spec.fk: parent}).afirst()
    if value is None:
        if existing is None:
            return RelatedObjectChange(relation=relation)
        status, pk = await _aremove_one_child(
            existing,
            spec,
            parent=parent,
            context=context,
            unlink=_unlinks_orphans(spec, relation=relation),
        )
        sync_relation_cache(parent, relation, None)
        return RelatedObjectChange(relation=relation, outcome=status, pk=pk)
    item = coerce_to_dict(value)
    row_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
    path = _RowPath(relation)
    if existing is None:
        row = await _acreate_row(
            {**item, **await _alink_values(spec, parent)},
            spec,
            path=path,
            seeds={"parent": parent},
            context=context,
            m2m=row_m2m,
        )
        outcome = RelationOutcome.CREATED
    else:
        row = await _aupdate_row(
            existing, item, spec, path=path, seeds={"parent": parent}, context=context, m2m=row_m2m
        )
        outcome = RelationOutcome.UPDATED
    sync_relation_cache(parent, relation, row)
    return RelatedObjectChange(relation=relation, outcome=outcome, pk=row.pk)


def _write_owned_collection(
    parent: Model,
    items: Any,
    spec: _CollectionSpec,
    *,
    relation: str,
    created: bool,
    context: Mapping[str, Any] | None,
) -> ChildCollectionChange:
    """Reconcile one owned collection against ``items``.

    Reverse foreign keys and generic relations share this loop whole: the link
    is the only thing that differs, one column or two, via
    ``_link_values`` on the way in and ``_link_fields`` on the way out.
    Both match inside the parent's own accessor, which is why neither takes a
    ``scope=``.
    """
    if items is UNSET:
        return ChildCollectionChange(relation=relation)
    existing_by_key: dict[Any, Model] = (
        {} if created else {getattr(e, spec.match_key): e for e in getattr(parent, relation).all()}
    )
    created_pks: list[Any] = []
    updated_pks: list[Any] = []
    matched: set[Any] = set()
    # Materialized, not streamed: a row's error names its position in the
    # incoming set, so the set needs a length before the first write.
    rows: list[dict[str, Any]] = [coerce_to_dict(i) for i in (items or [])]
    for index, item in enumerate(rows):
        child_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
        path = _RowPath(relation, index, len(rows))
        key = item.get(spec.match_key)
        if not _omitted(key) and key in existing_by_key:
            child = _update_row(
                existing_by_key[key],
                item,
                spec,
                path=path,
                seeds={"parent": parent},
                context=context,
                m2m=child_m2m,
            )
            updated_pks.append(child.pk)
            matched.add(key)
        else:
            child = _create_row(
                {**item, **_link_values(spec, parent)},
                spec,
                path=path,
                seeds={"parent": parent},
                context=context,
                m2m=child_m2m,
            )
            created_pks.append(child.pk)
    removals = _remove_orphans(
        existing_by_key, spec, matched, created, relation=relation, parent=parent, context=context
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

    That order is required: every target must hold a primary key before there
    is anything to link. Matching happens in ``scope=``, never in the current
    membership — the payload names the rows to link, which is precisely the set
    not linked yet, so matching against the members would make every new link
    look like a create.
    """
    if items is UNSET:
        return ChildCollectionChange(relation=relation)
    manager: Any = getattr(parent, relation)
    before: list[Any] = [] if created else list(manager.values_list("pk", flat=True))
    created_pks: list[Any] = []
    updated_pks: list[Any] = []
    targets: list[Any] = []
    # Materialized for the reason ``_write_owned_collection`` gives.
    rows: list[dict[str, Any]] = [coerce_to_dict(i) for i in (items or [])]
    for index, item in enumerate(rows):
        row_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
        path = _RowPath(relation, index, len(rows))
        match = _match_scoped_target(item, spec, relation=relation, context=context)
        if match is None:
            row = _create_row(
                item,
                spec,
                path=path,
                seeds={"parent": parent},
                context=context,
                m2m=row_m2m,
            )
            created_pks.append(row.pk)
        else:
            row = _update_row(
                match, item, spec, path=path, seeds={"parent": parent}, context=context, m2m=row_m2m
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

    A many-to-many target is shared, so ``deleted`` stays empty for this kind
    whatever ``mode`` says.
    """
    if spec.mode != "replace":
        return ()
    kept: set[Any] = {row.pk for row in targets}
    return tuple(pk for pk in before if pk not in kept)


def _create_row(
    data: dict[str, Any],
    spec: _RowSpec,
    *,
    path: _RowPath,
    seeds: dict[str, Any],
    context: Mapping[str, Any] | None,
    m2m: dict[str, Any] | None,
) -> Any:
    """Persist one new related row: ``create_service`` when declared, else the helper.

    The one create used by every kind, which is why ``data`` and ``seeds``
    arrive already built and why the primary-key guard sits here, ahead of the
    ``create_service`` dispatch: no kind can reach a create without passing it,
    and a declared service is not handed the key either. Keep the guard outside
    the ``try`` — it names the relation itself, which the block would then name
    a second time.
    """
    # Lazy import: genuine recursion cycle — the parent helpers call this loop,
    # and it calls them again for each row.
    from rest_framework_services.mutations.create_from_input import create_from_input

    _reject_unmatched_reference(data, spec, path.relation)
    try:
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
    except _ROW_WRITE_ERRORS as exc:
        raise _namespaced_row_error(exc, path) from exc


async def _acreate_row(
    data: dict[str, Any],
    spec: _RowSpec,
    *,
    path: _RowPath,
    seeds: dict[str, Any],
    context: Mapping[str, Any] | None,
    m2m: dict[str, Any] | None,
) -> Any:
    """Async variant of ``_create_row``."""
    # Lazy import: genuine recursion cycle — see ``_create_row``.
    from rest_framework_services.mutations.acreate_from_input import acreate_from_input

    _reject_unmatched_reference(data, spec, path.relation)
    try:
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
    except _ROW_WRITE_ERRORS as exc:
        raise _namespaced_row_error(exc, path) from exc
    return result.instance


def _update_row(
    instance: Model,
    data: dict[str, Any],
    spec: _RowSpec,
    *,
    path: _RowPath,
    seeds: dict[str, Any],
    context: Mapping[str, Any] | None,
    m2m: dict[str, Any] | None,
) -> Any:
    """Persist one matched row through ``update_service`` when declared.

    A service returning ``None`` means "use the in-memory instance".
    """
    # Lazy import: genuine recursion cycle — see ``_create_row``.
    from rest_framework_services.mutations.update_from_input import update_from_input

    try:
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
    except _ROW_WRITE_ERRORS as exc:
        raise _namespaced_row_error(exc, path) from exc
    return instance


async def _aupdate_row(
    instance: Model,
    data: dict[str, Any],
    spec: _RowSpec,
    *,
    path: _RowPath,
    seeds: dict[str, Any],
    context: Mapping[str, Any] | None,
    m2m: dict[str, Any] | None,
) -> Any:
    """Async variant of ``_update_row``."""
    # Lazy import: genuine recursion cycle — see ``_create_row``.
    from rest_framework_services.mutations.aupdate_from_input import aupdate_from_input

    try:
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
    except _ROW_WRITE_ERRORS as exc:
        raise _namespaced_row_error(exc, path) from exc
    return instance


def _remove_orphans(
    existing_by_key: dict[Any, Model],
    spec: _CollectionSpec,
    matched: set[Any],
    created: bool,
    *,
    relation: str,
    parent: Model,
    context: Mapping[str, Any] | None,
) -> list[tuple[RelationOutcome, Any]]:
    """Remove pre-update children not matched by the incoming set (replace mode).

    Iterates the *original* snapshot, never a fresh query, so children created
    in this same call are not taken for orphans.
    """
    removals: list[tuple[RelationOutcome, Any]] = []
    if created or spec.mode != "replace":
        return removals
    unlink = _unlinks_orphans(spec, relation=relation)
    for key, child in existing_by_key.items():
        if key in matched:
            continue
        removals.append(
            _remove_one_child(child, spec, parent=parent, context=context, unlink=unlink)
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
    """Async variant of ``apply_relations`` — same ordering, awaited."""
    collections: list[ChildCollectionChange] = []
    singular: list[RelatedObjectChange] = []
    for relation, spec in post_save_relations(relations):
        value = relation_data.get(relation, UNSET)
        if isinstance(spec, ChildSpec | GenericRelationSpec):
            collections.append(
                await _awrite_owned_collection(
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


async def _awrite_owned_collection(
    parent: Model,
    items: Any,
    spec: _CollectionSpec,
    *,
    relation: str,
    created: bool,
    context: Mapping[str, Any] | None,
) -> ChildCollectionChange:
    """Async variant of ``_write_owned_collection``."""
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
    # Materialized for the reason ``_write_owned_collection`` gives.
    rows: list[dict[str, Any]] = [coerce_to_dict(i) for i in (items or [])]
    for index, item in enumerate(rows):
        child_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
        path = _RowPath(relation, index, len(rows))
        key = item.get(spec.match_key)
        if not _omitted(key) and key in existing_by_key:
            child = await _aupdate_row(
                existing_by_key[key],
                item,
                spec,
                path=path,
                seeds={"parent": parent},
                context=context,
                m2m=child_m2m,
            )
            updated_pks.append(child.pk)
            matched.add(key)
        else:
            child = await _acreate_row(
                {**item, **await _alink_values(spec, parent)},
                spec,
                path=path,
                seeds={"parent": parent},
                context=context,
                m2m=child_m2m,
            )
            created_pks.append(child.pk)
    removals = await _aremove_orphans(
        existing_by_key, spec, matched, created, relation=relation, parent=parent, context=context
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
    """Async variant of ``_write_m2m_relation``."""
    if items is UNSET:
        return ChildCollectionChange(relation=relation)
    manager: Any = getattr(parent, relation)
    before: list[Any] = [] if created else await am2m_current_pks(parent, relation)
    created_pks: list[Any] = []
    updated_pks: list[Any] = []
    targets: list[Any] = []
    # Materialized for the reason ``_write_owned_collection`` gives.
    rows: list[dict[str, Any]] = [coerce_to_dict(i) for i in (items or [])]
    for index, item in enumerate(rows):
        row_m2m = dict(spec.m2m(item)) if spec.m2m is not None else None
        path = _RowPath(relation, index, len(rows))
        match = await _amatch_scoped_target(item, spec, relation=relation, context=context)
        if match is None:
            row = await _acreate_row(
                item,
                spec,
                path=path,
                seeds={"parent": parent},
                context=context,
                m2m=row_m2m,
            )
            created_pks.append(row.pk)
        else:
            row = await _aupdate_row(
                match, item, spec, path=path, seeds={"parent": parent}, context=context, m2m=row_m2m
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
    spec: _CollectionSpec,
    matched: set[Any],
    created: bool,
    *,
    relation: str,
    parent: Model,
    context: Mapping[str, Any] | None,
) -> list[tuple[RelationOutcome, Any]]:
    """Async variant of ``_remove_orphans``."""
    removals: list[tuple[RelationOutcome, Any]] = []
    if created or spec.mode != "replace":
        return removals
    unlink = _unlinks_orphans(spec, relation=relation)
    for key, child in existing_by_key.items():
        if key in matched:
            continue
        removals.append(
            await _aremove_one_child(child, spec, parent=parent, context=context, unlink=unlink)
        )
    return removals


def delete_relations(
    parent: Model,
    relations: Mapping[str, RelationSpec],
    *,
    context: Mapping[str, Any] | None = None,
) -> tuple[tuple[ChildCollectionChange, ...], tuple[RelatedObjectChange, ...]]:
    """Remove what ``parent`` owns, deepest first, before ``parent`` itself goes.

    Used by the default ``delete_model`` service to cascade explicitly where
    the database will not: a ``PROTECT`` relation, or a ``soft_delete`` hook
    Django never cascades through because no row is deleted.

    **One rule covers every kind: the cascade removes the rows the parent owns,
    and does nothing to the rows it merely points at.** So the owned kinds are
    removed, each after its own declared relations so a non-nullable grandchild
    goes first; a many-to-many loses only its membership; and a forward
    relation is reported untouched rather than refused, since the same map
    declares the write path and refusing it would make a good spec
    un-cascadable.
    """
    collections: list[ChildCollectionChange] = []
    singular: list[RelatedObjectChange] = []
    for relation, spec in relations.items():
        if isinstance(spec, ChildSpec | GenericRelationSpec):
            collections.append(
                _delete_owned_collection(parent, spec, relation=relation, context=context)
            )
        elif isinstance(spec, ManyToManySpec):
            collections.append(_clear_m2m_membership(parent, relation=relation))
        elif isinstance(spec, ReverseOneToOneSpec):
            singular.append(_delete_owned_row(parent, spec, relation=relation, context=context))
        elif isinstance(spec, ForwardRelationSpec):
            singular.append(RelatedObjectChange(relation=relation))
        else:
            raise _unknown_relation_kind(relation, spec)
    return (tuple(collections), tuple(singular))


async def adelete_relations(
    parent: Model,
    relations: Mapping[str, RelationSpec],
    *,
    context: Mapping[str, Any] | None = None,
) -> tuple[tuple[ChildCollectionChange, ...], tuple[RelatedObjectChange, ...]]:
    """Async variant of ``delete_relations`` — same rule, awaited."""
    collections: list[ChildCollectionChange] = []
    singular: list[RelatedObjectChange] = []
    for relation, spec in relations.items():
        if isinstance(spec, ChildSpec | GenericRelationSpec):
            collections.append(
                await _adelete_owned_collection(parent, spec, relation=relation, context=context)
            )
        elif isinstance(spec, ManyToManySpec):
            collections.append(await _aclear_m2m_membership(parent, relation=relation))
        elif isinstance(spec, ReverseOneToOneSpec):
            singular.append(
                await _adelete_owned_row(parent, spec, relation=relation, context=context)
            )
        elif isinstance(spec, ForwardRelationSpec):
            singular.append(RelatedObjectChange(relation=relation))
        else:
            raise _unknown_relation_kind(relation, spec)
    return (tuple(collections), tuple(singular))


def _delete_owned_collection(
    parent: Model,
    spec: _CollectionSpec,
    *,
    relation: str,
    context: Mapping[str, Any] | None,
) -> ChildCollectionChange:
    """Remove every row of one owned collection, its own relations first."""
    unlink = _unlinks_orphans(spec, relation=relation)
    nested = merge_relations(spec.children, spec.relations)
    removals: list[tuple[RelationOutcome, Any]] = []
    for child in getattr(parent, relation).all():
        delete_relations(child, nested, context=context)
        removals.append(
            _remove_one_child(child, spec, parent=parent, context=context, unlink=unlink)
        )
    return ChildCollectionChange(relation=relation, **_collect_removals(removals))


async def _adelete_owned_collection(
    parent: Model,
    spec: _CollectionSpec,
    *,
    relation: str,
    context: Mapping[str, Any] | None,
) -> ChildCollectionChange:
    """Async variant of ``_delete_owned_collection``."""
    unlink = _unlinks_orphans(spec, relation=relation)
    nested = merge_relations(spec.children, spec.relations)
    removals: list[tuple[RelationOutcome, Any]] = []
    async for child in getattr(parent, relation).all():
        await adelete_relations(child, nested, context=context)
        removals.append(
            await _aremove_one_child(child, spec, parent=parent, context=context, unlink=unlink)
        )
    return ChildCollectionChange(relation=relation, **_collect_removals(removals))


def _delete_owned_row(
    parent: Model,
    spec: ReverseOneToOneSpec,
    *,
    relation: str,
    context: Mapping[str, Any] | None,
) -> RelatedObjectChange:
    """Remove the parent's single reverse one-to-one row, if it has one."""
    row = spec.model.objects.filter(**{spec.fk: parent}).first()
    if row is None:
        return RelatedObjectChange(relation=relation)
    delete_relations(row, merge_relations(spec.children, spec.relations), context=context)
    status, pk = _remove_one_child(
        row, spec, parent=parent, context=context, unlink=_unlinks_orphans(spec, relation=relation)
    )
    return RelatedObjectChange(relation=relation, outcome=status, pk=pk)


async def _adelete_owned_row(
    parent: Model,
    spec: ReverseOneToOneSpec,
    *,
    relation: str,
    context: Mapping[str, Any] | None,
) -> RelatedObjectChange:
    """Async variant of ``_delete_owned_row``."""
    row = await spec.model.objects.filter(**{spec.fk: parent}).afirst()
    if row is None:
        return RelatedObjectChange(relation=relation)
    await adelete_relations(row, merge_relations(spec.children, spec.relations), context=context)
    status, pk = await _aremove_one_child(
        row, spec, parent=parent, context=context, unlink=_unlinks_orphans(spec, relation=relation)
    )
    return RelatedObjectChange(relation=relation, outcome=status, pk=pk)


def _clear_m2m_membership(parent: Model, *, relation: str) -> ChildCollectionChange:
    """Drop every member of one many-to-many, deleting no target row.

    The targets survive, so their own relations are not followed either.
    """
    manager: Any = getattr(parent, relation)
    members: tuple[Any, ...] = tuple(manager.values_list("pk", flat=True))
    manager.clear()
    return ChildCollectionChange(relation=relation, unlinked=members)


async def _aclear_m2m_membership(parent: Model, *, relation: str) -> ChildCollectionChange:
    """Async variant of ``_clear_m2m_membership``."""
    manager: Any = getattr(parent, relation)
    members: tuple[Any, ...] = tuple(await am2m_current_pks(parent, relation))
    await manager.aclear()
    return ChildCollectionChange(relation=relation, unlinked=members)
