"""``RelatedObjectChange`` — what a nested write did to one singular relation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_services.types.relation_outcome import RelationOutcome


@dataclass(frozen=True)
class RelatedObjectChange:
    """The delta for a relation that holds **one** row, not a collection.

    Carried in ``ChangeResult.relations``, one
    entry per singular relation declared in ``relations=`` — a forward
    foreign key / one-to-one, or a reverse one-to-one.
    [`ChildCollectionChange`][rest_framework_services.types.child_collection_change.ChildCollectionChange]'s four pk *tuples*
    cannot report a one-row relation honestly: every one of them would be
    either empty or a one-tuple, and "which of the four is non-empty" is a
    worse way to say "what happened" than saying it. So a singular relation
    reports one ``outcome`` and one ``pk``.

    ``outcome`` is a ``RelationOutcome``,
    which documents what each value means.

    ``pk`` is the primary key of the row the outcome is about, read
    *before* any delete (Django clears ``instance.pk`` afterwards), and
    ``None`` when no row was touched (``"untouched"`` / ``"cleared"``).
    """

    relation: str
    outcome: RelationOutcome = RelationOutcome.UNTOUCHED
    pk: Any = None

    def __bool__(self) -> bool:
        return self.outcome is not RelationOutcome.UNTOUCHED


__all__ = ["RelatedObjectChange"]
