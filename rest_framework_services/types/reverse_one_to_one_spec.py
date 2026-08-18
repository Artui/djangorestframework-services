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
    (``Profile.author``) and the parent (``Author``) reaches at most one of them through
    the reverse accessor. So it is the
    [`ChildSpec`][rest_framework_services.types.child_spec.ChildSpec] loop minus the
    collection, written in ``RelationPhase.REVERSE`` once the parent has a primary key
    to point at.

    Declared in ``relations={accessor_name: ReverseOneToOneSpec(...)}``, where
    the name is the parent's reverse accessor (``relations={"profile": ...}``
    for ``Author.profile``). The value at ``data[accessor_name]`` reads three
    ways. **Omitted** leaves the relation untouched. **``None``** removes the
    existing row, if any, by the ``orphan`` rule below — unlike a forward
    relation, this row *is* the parent's, so clearing the relation has to do
    something about it. **A mapping** updates the row when the parent already
    has one, and creates and links one when it does not.

    The existing row is found by querying ``fk`` rather than through the reverse
    accessor, so whatever the accessor had cached is a different Python object
    from the one that gets written. Each of the three cases leaves the parent
    agreeing with the write — pointed at the written row, or cleared where the
    row was removed — so the returned instance does not read a pre-write row.

    There is no ``match_key`` and no ``scope`` — the parent owns at most one row
    here, so the relation itself is the match — and no ``mode``: a one-row
    relation has no orphans beyond the ``None`` case, which is explicit.

    Attributes:
        model: The related model class.
        fk: The name of that model's field pointing at the parent (``"author"``
            for ``Profile.author``). Set automatically on creation, and the
            field whose nullability decides unlink-versus-delete by default.
        field_map: Forwarded to the row's own ``create_from_input`` /
            ``update_from_input`` call.
        exclude_fields: Forwarded likewise.
        m2m: Forwarded likewise.
        children: Forwarded likewise.
        relations: Forwarded likewise.
        create_service: Optional service replacing that call for the row, with
            the contract [`ChildSpec`][rest_framework_services.types.child_spec.ChildSpec] states: it
            receives ``parent``, and its ``data`` already carries the ``fk``.
            Declaring it alongside the row-shaping fields above raises at
            construction.
        update_service: The same; returning ``None`` means "use the in-memory
            instance".
        delete_service: Replaces the unlink-or-delete rule below, so the outcome
            is reported as ``"removed"`` — the only thing still known — and an
            explicit ``orphan`` beside it raises.
        orphan: What removing the row *does*, by
            [`ChildSpec`][rest_framework_services.types.child_spec.ChildSpec]'s rule: ``"auto"`` (the
            default) derives it from ``fk`` — **unlinked** when that field is
            nullable (like ``on_delete=SET_NULL``), **deleted** when it is not
            (like ``CASCADE``) — while ``"unlink"`` / ``"delete"`` state it
            instead, and ``"unlink"`` against a non-nullable ``fk`` raises
            ``ImproperlyConfigured`` at write time.
            It covers both removals there are: the ``None`` case above and the
            [`delete_model`][rest_framework_services.services.delete_model.delete_model] cascade.
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
