"""``RelatedObjectChange`` — what a nested write did to one singular relation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RelatedObjectChange:
    """The delta for a relation that holds **one** row, not a collection.

    Carried in :attr:`~rest_framework_services.ChangeResult.relations`, one
    entry per singular relation declared in ``relations=`` — a forward
    foreign key / one-to-one, or a reverse one-to-one.
    :class:`~rest_framework_services.ChildCollectionChange`'s four pk *tuples*
    cannot report a one-row relation honestly: every one of them would be
    either empty or a one-tuple, and "which of the four is non-empty" is a
    worse way to say "what happened" than saying it. So a singular relation
    reports one :attr:`outcome` and one :attr:`pk`.

    :attr:`outcome` is one of:

    - ``"untouched"`` — the input omitted the relation (or asked to clear one
      that was already empty). Nothing was read or written; this is also what
      :meth:`__bool__` reports as falsy.
    - ``"created"`` — a new row was written and linked.
    - ``"updated"`` — an existing row was matched and written.
    - ``"cleared"`` — a *forward* relation was set to ``None``. The parent's
      foreign-key column changed; the row it used to point at was not touched,
      because a forward target is not owned by the parent (it may be shared).
      :attr:`pk` is ``None`` — nothing happened to a row.
    - ``"unlinked"`` — a *reverse* row's foreign key was set to ``None``,
      because the foreign key is nullable (mirroring ``on_delete=SET_NULL``).
    - ``"deleted"`` — a *reverse* row was deleted, because its foreign key is
      not nullable (mirroring ``on_delete=CASCADE``).
    - ``"removed"`` — a *reverse* row was handed to the spec's
      ``delete_service``. ⚠ Deliberately **not** ``"deleted"``: once a service
      owns the row, the loop no longer knows whether the row was deleted,
      archived, unlinked or left standing, and reporting a guess as fact is
      worse than reporting the one thing that is true — the loop removed the
      row from the relation and a service decided the rest.

    :attr:`pk` is the primary key of the row the outcome is about, read
    *before* any delete (Django clears ``instance.pk`` afterwards), and
    ``None`` when no row was touched (``"untouched"`` / ``"cleared"``).
    """

    relation: str
    outcome: str = "untouched"
    pk: Any = None

    def __bool__(self) -> bool:
        return self.outcome != "untouched"


__all__ = ["RelatedObjectChange"]
