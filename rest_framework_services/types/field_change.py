"""A single recorded field-level change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FieldChange:
    """One field's before/after pair from a mutation.

    ``old`` will be ``UNSET`` for fields populated as part of a ``create``
    (no prior value existed).
    """

    field: str
    old: Any
    new: Any
