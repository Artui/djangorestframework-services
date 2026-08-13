"""The structured result returned by every mutation helper."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from django.db.models import Model

from rest_framework_services.types.child_collection_change import ChildCollectionChange
from rest_framework_services.types.field_change import FieldChange
from rest_framework_services.types.related_object_change import RelatedObjectChange

ModelT = TypeVar("ModelT", bound=Model)


@dataclass(frozen=True)
class ChangeResult(Generic[ModelT]):
    """Outcome of a mutation helper call.

    ``instance`` is the model instance after the mutation. ``created`` is True
    iff this came from [`create_from_input`][rest_framework_services.mutations.create_from_input.create_from_input] / [`acreate_from_input`][rest_framework_services.mutations.acreate_from_input.acreate_from_input].
    ``changes`` records every field whose value actually differed from its
    prior value (or from ``UNSET`` for creates). ``children`` carries one
    [`ChildCollectionChange`][rest_framework_services.types.child_collection_change.ChildCollectionChange] per reverse-FK collection written via the
    ``children=`` argument — empty for the common no-nested-write case.

    ``relations`` carries one [`RelatedObjectChange`][rest_framework_services.types.related_object_change.RelatedObjectChange] per **singular**
    relation written via ``relations=`` (forward FK / one-to-one, reverse
    one-to-one). The split is by shape, not by keyword: a collection reports
    tuples of pks and a one-row relation reports an outcome, so they are
    different carriers, and a reverse-FK collection declared through
    ``relations=`` still reports under ``children`` exactly as it does through
    ``children=``. A forward relation shows up **twice** and means two
    different things — here as the row that was created or matched, and in
    ``changes`` as the parent's foreign-key column, which only appears if it
    actually changed.

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
    relations: tuple[RelatedObjectChange, ...] = field(default_factory=tuple)

    @property
    def changed_fields(self) -> tuple[str, ...]:
        """Names of every field present in ``changes``."""
        return tuple(change.field for change in self.changes)

    def get_field_change(self, field_name: str) -> FieldChange | None:
        """Return the [`FieldChange`][rest_framework_services.types.field_change.FieldChange] for ``field_name``, or ``None``."""
        for change in self.changes:
            if change.field == field_name:
                return change
        return None

    def get_child_change(self, relation: str) -> ChildCollectionChange | None:
        """Return the [`ChildCollectionChange`][rest_framework_services.types.child_collection_change.ChildCollectionChange] for ``relation``, or ``None``."""
        for change in self.children:
            if change.relation == relation:
                return change
        return None

    def get_relation_change(self, relation: str) -> RelatedObjectChange | None:
        """Return the [`RelatedObjectChange`][rest_framework_services.types.related_object_change.RelatedObjectChange] for ``relation``, or ``None``."""
        for change in self.relations:
            if change.relation == relation:
                return change
        return None

    def __bool__(self) -> bool:
        return bool(self.changes or any(self.children) or any(self.relations))
