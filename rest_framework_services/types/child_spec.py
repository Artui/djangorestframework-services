"""``ChildSpec`` — declarative reverse-FK child-collection write configuration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from django.db.models import Model

from rest_framework_services.types.relation_mode import RelationMode
from rest_framework_services.types.relation_orphan import RelationOrphan
from rest_framework_services.types.relation_phase import RelationPhase
from rest_framework_services.types.relation_spec import RelationSpec
from rest_framework_services.types.utils import (
    validate_relation_mode,
    validate_relation_orphan,
    validate_relation_services,
)


@dataclass(frozen=True)
class ChildSpec(RelationSpec):
    """How to persist one reverse-FK ("one-to-many") child collection.

    The reverse-FK member of the relation taxonomy: it writes rows whose
    foreign key points back at the parent, so it belongs to
    ``RelationPhase.REVERSE`` and is written after
    the parent's ``save()``.

    Passed in the ``relations={relation_name: ChildSpec(...)}`` map of
    [`create_from_input`][rest_framework_services.mutations.create_from_input.create_from_input]
    /
    [`update_from_input`][rest_framework_services.mutations.update_from_input.update_from_input]
    (and their async siblings) — or in ``children=``, which is the same thing under the
    name it shipped as — and forwarded by the default
    [`create_model`][rest_framework_services.services.create_model.create_model] /
    [`update_model`][rest_framework_services.services.update_model.update_model] /
    [`delete_model`][rest_framework_services.services.delete_model.delete_model]
    services. The incoming child rows are read from ``data[relation_name]``; each child
    is persisted by running it back through the same mutation helpers, so scalar / m2m /
    nested semantics compose recursively. The whole parent + children write runs inside
    the service's atomic block; field-level validation stays in the input serializer /
    dataclass — the helper owns persistence only.

    **Pluggable services — the spec owns reconciliation, the service owns the row.**
    Matching, ``mode`` and orphan handling never move into your code; a slot is called
    once per row the loop has already decided about. Each is invoked through
    [`run_service`][rest_framework_services.services.run_service.run_service] /
    [`arun_service`][rest_framework_services.services.arun_service.arun_service] with
    ``atomic=False``, because the surrounding service's atomic block already wraps the
    whole tree and letting each row open its own would mean a savepoint per row. Each
    receives only the pool keys it declares (the library's usual signature-filtering
    idiom), drawn from the mutation helpers' opaque ``context=`` plus the loop's own
    seeds. Those seeds — ``data`` / ``instance`` / ``parent`` — are applied **after**
    the context, so a context key of the same name cannot outrank them, the precedence
    form of the rule ``RESERVED_POOL_SEEDS`` states for the dispatcher's pools. In the
    async loops the slot must be an ``async def``: the async path is awaited end to end.

    A declared slot owns that row **entirely**: ``field_map``,
    ``exclude_fields``, ``m2m`` and the nested ``children`` / ``relations``
    maps configure the default mutation-helper call, so a ``create_service`` /
    ``update_service`` standing in for it makes them dead configuration.
    Declaring both raises
    ``ImproperlyConfigured`` at construction rather
    than dropping them quietly. ``delete_service`` is exempt — it replaces the
    unlink-or-delete rule, not the helper call, so the cascade still removes a
    row's grandchildren before handing the row over. The spec keeps only what
    it never delegates — which rows exist, which incoming row matches which
    existing one, and what happens to the ones left over.

    Attributes:
        model: The child model class.
        fk: Name of the child's forward foreign-key field pointing at the
            parent (``"author"`` for ``Book.author``). Set automatically on
            created children, and used to resolve the parent's reverse manager.
        match_key: Field used to pair an incoming row with an existing child.
            An incoming row whose ``match_key`` matches an existing child
            updates it; one with no match, or no key, is created. The same name
            is read off both the incoming mapping (``item[match_key]``) and the
            existing instance (``getattr(child, match_key)``), so serializers
            emitting ``"id"`` should set ``match_key="id"``.
        mode: ``"replace"`` matches incoming to existing, creates new, updates
            matched, and removes orphans (existing children absent from the
            incoming set); ``"merge"`` upserts only and never removes.
        field_map: Forwarded to the per-child ``create_from_input`` /
            ``update_from_input`` call, exactly as for the parent.
        exclude_fields: Forwarded to the per-child call, as ``field_map`` is.
        m2m: Callable ``(child_row) -> mapping`` deriving the child's
            many-to-many assignments from its incoming row — the per-child
            analogue of [`create_model`][rest_framework_services.services.create_model.create_model]'s ``m2m``.
        children: Nested ``{relation_name: ChildSpec}`` map for grandchildren;
            recursion follows the declared tree, so depth is bounded by how
            deeply you nest specs.
        relations: The same nesting for every other relation kind — a
            ``{relation_name: RelationSpec}`` map applied to each child row
            exactly as the top-level ``relations=`` is applied to the parent.
        create_service: Per-row service replacing the default mutation-helper
            call, for a child whose write has real behaviour (side effects,
            derived columns, events, an external call). Called as
            ``create_service(*, data, parent, **extras)``, where ``data`` is
            the incoming row with the ``fk`` already pointing at ``parent``,
            since linking the child *is* reconciliation. Must return the
            created row; the loop reads its pk for the delta.
        update_service: The same for updates, called as
            ``update_service(*, instance, data, parent, **extras)``. Returning
            ``None`` means "use the in-memory instance", the framework's
            existing convention.
        delete_service: Called as
            ``delete_service(*, instance, parent, **extras)``, replacing the
            unlink-or-delete rule for that row — both for orphan removal and
            for the [`delete_model`][rest_framework_services.services.delete_model.delete_model] cascade. The
            loop can no longer tell an unlink from a delete, so the pk is
            reported under
            ``ChildCollectionChange.removed``
            rather than guessed into one of the two. It *is* the disposal, so
            declaring it beside an explicit ``orphan`` raises at construction:
            the flag would decide nothing.
        orphan: What removing an orphan *does*, where ``mode`` says whether one
            is removed at all. ``"auto"`` derives it from the schema:
            **unlinked** (its ``fk`` set to ``None``) when the FK is nullable,
            else **deleted**, mirroring ``on_delete=SET_NULL`` vs ``CASCADE``.
            ``"unlink"`` and ``"delete"`` say it outright, for a spec that means
            one of them rather than whichever the column happens to allow — a
            later migration adding ``null=True`` would otherwise turn a
            destructive ``"replace"`` into a non-destructive one with nothing in
            the spec changing. ``"unlink"`` against a non-nullable FK raises
            ``ImproperlyConfigured`` when the
            relation is written, since there is no link to blank. The same rule
            governs the [`delete_model`][rest_framework_services.services.delete_model.delete_model] cascade,
            which disposes of the same rows.
    """

    write_phase: ClassVar[RelationPhase] = RelationPhase.REVERSE

    model: type[Model]
    fk: str
    match_key: str = "pk"
    mode: RelationMode | str = RelationMode.REPLACE
    field_map: dict[str, str] | None = None
    exclude_fields: list[str] | None = None
    m2m: Callable[[Any], Mapping[str, Any]] | None = None
    children: Mapping[str, ChildSpec] | None = None
    relations: Mapping[str, RelationSpec] | None = None
    create_service: Callable[..., Any] | None = None
    update_service: Callable[..., Any] | None = None
    delete_service: Callable[..., Any] | None = None
    # Declared last, beneath the services it is checked against, so adding it
    # does not renumber the positional arguments of a spec class that shipped.
    orphan: RelationOrphan | str = RelationOrphan.AUTO

    def __post_init__(self) -> None:
        validate_relation_mode(self.mode, label="ChildSpec")
        validate_relation_orphan(self.orphan, delete_service=self.delete_service, label="ChildSpec")
        validate_relation_services(
            label="ChildSpec",
            services={
                "create_service": self.create_service,
                "update_service": self.update_service,
            },
            shaping={
                "field_map": self.field_map,
                "exclude_fields": self.exclude_fields,
                "m2m": self.m2m,
                "children": self.children,
                "relations": self.relations,
            },
        )


__all__ = ["ChildSpec"]
