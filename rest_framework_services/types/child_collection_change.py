"""``ChildCollectionChange`` — per-collection deltas from a nested write."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ChildCollectionChange:
    """What a nested write did to one reverse-FK child collection.

    Carried in :attr:`~rest_framework_services.ChangeResult.children`, one
    entry per ``children=`` relation. The tuples hold child **primary keys**:

    - **``created``** — children inserted.
    - **``updated``** — existing children whose row was updated (matched by the
      :class:`~rest_framework_services.ChildSpec`'s ``match_key``).
    - **``deleted``** — orphaned children removed because their FK is
      non-nullable.
    - **``unlinked``** — orphaned children detached (FK set to ``None``) because
      their FK is nullable.

    ``updated`` records every matched child the helper ran through
    ``update_from_input``, regardless of whether that child's own columns
    actually changed.
    """

    relation: str
    created: tuple[Any, ...] = field(default_factory=tuple)
    updated: tuple[Any, ...] = field(default_factory=tuple)
    deleted: tuple[Any, ...] = field(default_factory=tuple)
    unlinked: tuple[Any, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return bool(self.created or self.updated or self.deleted or self.unlinked)


__all__ = ["ChildCollectionChange"]
