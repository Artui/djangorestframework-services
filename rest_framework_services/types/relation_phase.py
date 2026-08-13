"""``RelationPhase`` — when a relation kind is written, relative to the parent."""

from __future__ import annotations

from enum import IntEnum


class RelationPhase(IntEnum):
    """The slot in the write sequence a relation kind belongs to.

    A nested write cannot honour the order the ``relations=`` mapping happens
    to be spelled in: a forward foreign key has to be resolved *before* the
    parent row exists, and a many-to-many has to be assigned *after* it does.
    So the order is a property of the relation **kind** — each spec class
    declares its phase as a ``write_phase`` class attribute (see
    [`RelationSpec`][rest_framework_services.types.relation_spec.RelationSpec]) and the
    driver walks the phases in this enum's order, never the mapping's:

    1. ``FORWARD`` — the FK column lives on the parent, so the target row
       must exist before the parent is saved. Writing it first also means the
       assignment is an ordinary column change, picked up by the same diff and
       ``update_fields`` machinery as any other field.
    2. *the parent's* ``save()`` — not a phase; the boundary the phases are
       named around.
    3. ``REVERSE`` — the FK column lives on the other row (reverse FK
       collections and reverse one-to-ones), so the parent must have a primary
       key to point at.
    4. ``GENERIC`` — generic relations, which need the saved parent's
       content type *and* primary key.
    5. ``M2M`` — through-table writes, which need both rows saved.

    Declaration order still decides everything the phases leave open: two
    relations in the same phase are written in the order they were declared.

    The member values order the phases and nothing else; compare and sort by
    the members, never by the numbers.
    """

    FORWARD = 0
    REVERSE = 1
    GENERIC = 2
    M2M = 3


__all__ = ["RelationPhase"]
