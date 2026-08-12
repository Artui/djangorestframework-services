"""``RelationMode`` — whether an incoming collection is authoritative."""

from __future__ import annotations

from enum import Enum


class RelationMode(str, Enum):
    """How a relation reconciles the rows the payload leaves out.

    Shared by every kind that reconciles a collection, so the two words mean the
    same thing on all of them. What "dispose of" means is the kind's own
    business — a child row is unlinked or deleted, a many-to-many target is only
    dropped from the relation — but *when* it happens is this one flag.

    Inheriting from ``str`` keeps the value JSON-serializable and means the
    plain strings this field accepted before it was an enum still work, in a
    comparison and as an argument.
    """

    REPLACE = "replace"
    """The incoming set is authoritative; rows it leaves out are disposed of."""

    MERGE = "merge"
    """The incoming set upserts and removes nothing."""


__all__ = ["RelationMode"]
