"""The structured result returned by every mutation helper."""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Model

from rest_framework_services.types.field_change import FieldChange


@dataclass(frozen=True)
class ChangeResult:
    """Outcome of a mutation helper call.

    ``instance`` is the model instance after the mutation. ``created`` is True
    iff this came from :func:`create_from_input` / :func:`acreate_from_input`.
    ``changes`` records every field whose value actually differed from its
    prior value (or from ``UNSET`` for creates).
    """

    instance: Model
    created: bool
    changes: tuple[FieldChange, ...]

    @property
    def changed_fields(self) -> tuple[str, ...]:
        """Names of every field present in :attr:`changes`."""
        return tuple(change.field for change in self.changes)

    def get_field_change(self, field_name: str) -> FieldChange | None:
        """Return the :class:`FieldChange` for ``field_name``, or ``None``."""
        for change in self.changes:
            if change.field == field_name:
                return change
        return None

    def __bool__(self) -> bool:
        return bool(self.changes)
