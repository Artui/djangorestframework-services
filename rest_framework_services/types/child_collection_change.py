"""``ChildCollectionChange`` — per-collection deltas from a nested write."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ChildCollectionChange:
    """What a nested write did to one reverse-FK child collection.

    Carried in ``ChangeResult.children``, one
    entry per ``children=`` relation. The tuples hold child **primary keys**:

    - **``created``** — children inserted.
    - **``updated``** — existing children whose row was updated (matched by the
      [`ChildSpec`][rest_framework_services.types.child_spec.ChildSpec]'s ``match_key``).
    - **``deleted``** — orphaned children removed because their FK is
      non-nullable.
    - **``unlinked``** — orphaned children detached (FK set to ``None``) because
      their FK is nullable.
    - **``removed``** — children handed to the spec's ``delete_service``.
      Deliberately a fifth tuple rather than a reuse of ``deleted``: once a
      service owns the row, the loop no longer knows whether it was deleted,
      archived, unlinked or left standing, and folding those into ``deleted``
      would report a guess as fact. What the loop does know is that the row
      left the relation and a service decided the rest.

    ``updated`` records every matched child the helper ran through
    ``update_from_input``, regardless of whether that child's own columns
    actually changed.
    """

    relation: str
    created: tuple[Any, ...] = field(default_factory=tuple)
    updated: tuple[Any, ...] = field(default_factory=tuple)
    deleted: tuple[Any, ...] = field(default_factory=tuple)
    unlinked: tuple[Any, ...] = field(default_factory=tuple)
    removed: tuple[Any, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return bool(self.created or self.updated or self.deleted or self.unlinked or self.removed)


__all__ = ["ChildCollectionChange"]
