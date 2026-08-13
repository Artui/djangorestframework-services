"""``RelationOutcome`` — what a nested write did to one singular relation."""

from __future__ import annotations

from enum import Enum


class RelationOutcome(str, Enum):
    """The single fact a [`RelatedObjectChange`][rest_framework_services.types.related_object_change.RelatedObjectChange] reports.

    A collection reports four tuples of primary keys, which is the honest shape
    for many rows and a poor one for a relation that holds exactly one: every
    tuple would be empty or a one-tuple, and "which of the four is non-empty" is
    a worse way to say what happened than saying it.

    Inheriting from ``str`` keeps the value JSON-serializable and lets a caller
    compare against the plain string, matching
    [`SelectorKind`][rest_framework_services.types.selector_kind.SelectorKind].
    """

    UNTOUCHED = "untouched"
    """The input omitted the relation, or asked to clear one already empty.

    Nothing was read or written, and this is what :meth:`RelatedObjectChange.
    __bool__` reports as falsy.
    """

    CREATED = "created"
    """A new row was written and linked."""

    UPDATED = "updated"
    """An existing row was matched and written."""

    CLEARED = "cleared"
    """A forward relation was set to ``None``.

    The parent's foreign-key column changed and the row it used to point at was
    not touched, because a forward target is not owned by the parent and may be
    shared. The change carries no primary key: nothing happened to a row.
    """

    UNLINKED = "unlinked"
    """A reverse row's foreign key was set to ``None``.

    Because the foreign key is nullable, mirroring ``on_delete=SET_NULL``. Also
    what a dropped many-to-many member reports, since the target is shared and
    only the membership went.
    """

    DELETED = "deleted"
    """A reverse row was deleted, because its foreign key is not nullable.

    Mirroring ``on_delete=CASCADE``.
    """

    REMOVED = "removed"
    """A row was handed to the spec's ``delete_service``.

    Deliberately not ``DELETED``. Once a service owns the row the loop no
    longer knows whether it was deleted, archived, unlinked or left standing,
    and reporting a guess as fact is worse than reporting the one thing that is
    true: the loop removed the row from the relation and a service decided the
    rest.
    """


__all__ = ["RelationOutcome"]
