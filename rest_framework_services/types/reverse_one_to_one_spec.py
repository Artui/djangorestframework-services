"""``ReverseOneToOneSpec`` — the singular-row variant of the children loop."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from django.db.models import Model

from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.relation_orphan import RelationOrphan
from rest_framework_services.types.relation_phase import RelationPhase
from rest_framework_services.types.relation_spec import RelationSpec
from rest_framework_services.types.utils import (
    validate_relation_orphan,
    validate_relation_services,
)


@dataclass(frozen=True)
class ReverseOneToOneSpec(RelationSpec):
    """How to persist a **reverse** one-to-one — the row that points *back*.

    The other side of a ``OneToOneField``: the column lives on the related row
    (``Profile.author``) and the parent (``Author``) reaches at most one of
    them through the reverse accessor. So the write belongs to
    :attr:`~rest_framework_services.RelationPhase.REVERSE`, after the parent
    has a primary key to point at — the same phase as
    :class:`~rest_framework_services.ChildSpec`, and the same loop, minus the
    collection.

    Declared in ``relations={accessor_name: ReverseOneToOneSpec(...)}``, where
    the name is the parent's reverse accessor (``relations={"profile": ...}``
    for ``Author.profile``). The value at ``data[accessor_name]`` reads three
    ways:

    - **omitted** — the relation is untouched.
    - **``None``** — the existing row, if any, is removed by the same rule the
      children loop uses for orphans: **unlinked** when ``fk`` is nullable
      (like ``on_delete=SET_NULL``), **deleted** when it is not (like
      ``CASCADE``). Unlike a forward relation, this row *is* the parent's —
      clearing the relation has to do something about it.
    - **a mapping** — the row is updated when the parent already has one, and
      created and linked when it does not.

    Fields:

    - **``model``** — the related model class.
    - **``fk``** — the name of that model's field pointing at the parent
      (``"author"`` for ``Profile.author``). Set automatically on creation, and
      the field whose nullability decides unlink-versus-delete by default.
    - **``orphan``** — what removing the row *does*: ``"auto"`` (the default)
      derives it from that field, and ``"unlink"`` / ``"delete"`` state it, for
      a spec that means one of them rather than whichever the column happens to
      allow. ``"unlink"`` against a non-nullable ``fk`` raises
      :exc:`~django.core.exceptions.ImproperlyConfigured` when the relation is
      written. The rule covers both removals there are — the ``None`` case here
      and the :func:`~rest_framework_services.delete_model` cascade.
    - **``field_map``** / **``exclude_fields``** / **``m2m``** /
      **``children``** / **``relations``** — forwarded to the row's own
      ``create_from_input`` / ``update_from_input`` call.
    - **``create_service``** / **``update_service``** / **``delete_service``**
      — optional services replacing that call for the row, with the same
      contract as :class:`~rest_framework_services.ChildSpec`'s: they receive
      ``parent``, a create service's ``data`` already carries the ``fk``, an
      update service returning ``None`` means "use the in-memory instance",
      and a delete service replaces the unlink-or-delete rule (so the outcome
      is reported as ``"removed"``, the only thing still known, and an explicit
      ``orphan`` beside it raises). Declaring ``create_service`` /
      ``update_service`` alongside the row-shaping knobs raises at
      construction.

    There is no ``match_key`` and no ``scope``: the parent owns at most one
    row here, so there is nothing to match and nothing to scope — the relation
    itself is the match. There is no ``mode`` either: a one-row relation has no
    orphans beyond the ``None`` case, which is explicit.
    """

    write_phase: ClassVar[RelationPhase] = RelationPhase.REVERSE

    model: type[Model]
    fk: str
    field_map: dict[str, str] | None = None
    exclude_fields: list[str] | None = None
    m2m: Callable[[Any], Mapping[str, Any]] | None = None
    children: Mapping[str, ChildSpec] | None = None
    relations: Mapping[str, RelationSpec] | None = None
    create_service: Callable[..., Any] | None = None
    update_service: Callable[..., Any] | None = None
    delete_service: Callable[..., Any] | None = None
    # Declared last, for the reason given on ``ChildSpec``.
    orphan: RelationOrphan | str = RelationOrphan.AUTO

    def __post_init__(self) -> None:
        validate_relation_orphan(
            self.orphan, delete_service=self.delete_service, label="ReverseOneToOneSpec"
        )
        validate_relation_services(
            label="ReverseOneToOneSpec",
            services={
                "create_service": self.create_service,
                "update_service": self.update_service,
            },
            shaping={
                "field_map": self.field_map,
                "exclude_fields": self.exclude_fields,
                "m2m": self.m2m,
                "children": self.children,
                "relations": self.relations,
            },
        )


__all__ = ["ReverseOneToOneSpec"]
