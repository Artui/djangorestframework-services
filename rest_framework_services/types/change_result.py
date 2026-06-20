"""The structured result returned by every mutation helper."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from django.db.models import Model

from rest_framework_services.types.child_collection_change import ChildCollectionChange
from rest_framework_services.types.field_change import FieldChange

ModelT = TypeVar("ModelT", bound=Model)


@dataclass(frozen=True)
class ChangeResult(Generic[ModelT]):
    """Outcome of a mutation helper call.

    ``instance`` is the model instance after the mutation. ``created`` is True
    iff this came from :func:`create_from_input` / :func:`acreate_from_input`.
    ``changes`` records every field whose value actually differed from its
    prior value (or from ``UNSET`` for creates). ``children`` carries one
    :class:`ChildCollectionChange` per reverse-FK collection written via the
    ``children=`` argument — empty for the common no-nested-write case.

    The class is generic over the concrete model type: callers that pass
    ``Author`` into a mutation helper get back a ``ChangeResult[Author]``
    whose ``.instance`` is typed as ``Author``. The bare name
    ``ChangeResult`` (no parameter) resolves to ``ChangeResult[Model]`` and
    keeps working for callers that don't care.
    """

    instance: ModelT
    created: bool
    changes: tuple[FieldChange, ...]
    children: tuple[ChildCollectionChange, ...] = field(default_factory=tuple)

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

    def get_child_change(self, relation: str) -> ChildCollectionChange | None:
        """Return the :class:`ChildCollectionChange` for ``relation``, or ``None``."""
        for change in self.children:
            if change.relation == relation:
                return change
        return None

    def __bool__(self) -> bool:
        return bool(self.changes or any(self.children))
