"""``RelationSpec`` — the base every nested-write relation spec shares."""

from __future__ import annotations

from typing import ClassVar

from rest_framework_services.types.relation_phase import RelationPhase


class RelationSpec:
    """What the nested-write driver needs from a relation spec: its phase.

    The ``relations={name: spec}`` mapping of the mutation helpers takes one
    spec per relation, and the kinds differ in almost everything — a forward
    foreign key has no orphans, a reverse one-to-one has no collection, a
    child collection has both. What they share is that **the driver must know
    when to write each one**, and that answer belongs to the class rather than
    to the instance: every forward foreign key is written before the parent's
    ``save()`` because it is a forward foreign key, not because a particular
    spec asked to be.

    So each concrete spec declares ``write_phase`` as a class attribute and
    the driver orders by it (see [`RelationPhase`][rest_framework_services.types.relation_phase.RelationPhase] for the sequence). A
    spec author never sets it per-instance, and the mapping's insertion order
    cannot reorder the phases — only the relations *within* one.

    Subclasses are frozen dataclasses; this base deliberately declares no
    fields, so each kind spells out its own and no kind inherits a knob that
    means nothing for it.
    """

    write_phase: ClassVar[RelationPhase]


__all__ = ["RelationSpec"]
