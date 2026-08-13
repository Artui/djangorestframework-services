"""``RelationOrphan`` — what becomes of a row the relation lets go."""

from __future__ import annotations

from enum import Enum


class RelationOrphan(str, Enum):
    """How a relation disposes of a row it no longer holds.

    Shared by the kinds that own their rows — a reverse-FK collection, a
    generic relation, a reverse one-to-one — so the word means the same thing on
    all three. It answers *what* happens to a row that is let go;
    ``RelationMode`` answers *when* a row is let go at all, and the two are
    independent knobs on purpose.

    The default derives the answer from the schema, as it always has. The other
    two exist because a derived answer is not a stated one: whether the link can
    hold ``NULL`` is a fact about a column, and a migration adding ``null=True``
    later would silently turn a ``replace`` that deleted into one that unlinks,
    with nothing in the spec — or in its tests — changing to say so. A spec that
    means to delete says so.

    Inheriting from ``str`` keeps the value JSON-serializable and means a plain
    string works wherever the member does, matching ``RelationMode`` and
    ``RelationOutcome``.
    """

    AUTO = "auto"
    """Derive it from the link: unlink when it can hold ``NULL``, else delete.

    Mirroring ``on_delete=SET_NULL`` versus ``CASCADE``, which is the better
    default because it honours what the model already declares.
    """

    UNLINK = "unlink"
    """Always blank the row's link to the parent and leave the row standing.

    Refused when the link cannot hold ``NULL`` — there is nothing to blank, and
    deleting the row instead would be the opposite of what was asked.
    """

    DELETE = "delete"
    """Always delete the row, whether or not its link could have been blanked."""


__all__ = ["RelationOrphan"]
