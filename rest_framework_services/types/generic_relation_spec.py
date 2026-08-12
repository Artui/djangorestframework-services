"""``GenericRelationSpec`` — rows linked to the parent by a content type."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from django.db.models import Model

from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.relation_mode import RelationMode
from rest_framework_services.types.relation_orphan import RelationOrphan
from rest_framework_services.types.relation_phase import RelationPhase
from rest_framework_services.types.relation_spec import RelationSpec
from rest_framework_services.types.utils import (
    validate_relation_mode,
    validate_relation_orphan,
    validate_relation_services,
)


@dataclass(frozen=True)
class GenericRelationSpec(RelationSpec):
    """How to persist a ``GenericRelation`` — a collection linked by content type.

    The reverse-FK collection with the foreign key replaced by a pair of
    columns: a ``ForeignKey`` to ``ContentType`` saying *which model* the row
    belongs to, and an id column saying *which row*. It reconciles exactly as
    :class:`~rest_framework_services.ChildSpec` does — matched inside the
    parent's own accessor, so no ``scope=`` is needed or accepted — and is
    written in :attr:`~rest_framework_services.RelationPhase.GENERIC`, once the
    parent's ``save()`` has given it both a content type and a primary key.

    Declared in ``relations={accessor_name: GenericRelationSpec(...)}``, where
    the name is the ``GenericRelation`` declared on the parent
    (``relations={"attachments": ...}`` for ``Catalog.attachments``). A
    relation the input omits is untouched; an explicit ``[]`` in ``"replace"``
    mode empties it.

    **This kind needs ``django.contrib.contenttypes`` in ``INSTALLED_APPS``**,
    and nothing else in the library does. Declaring the spec is always safe;
    *writing* one without the app installed raises
    :exc:`~django.core.exceptions.ImproperlyConfigured` naming the remedy.

    Attributes:
        model: The related model class — the one carrying the content-type and
            id columns, e.g. ``Attachment``.
        content_type_field: Name of the content-type column, defaulting to
            Django's own ``"content_type"``.
        object_id_field: Name of the id column, defaulting to ``"object_id"``.
            Both mirror the ``GenericRelation`` arguments of the same name; set
            them when the model spells the columns differently.
        match_key: The field pairing an incoming row with an existing one
            (default ``"pk"``), read inside the parent's own accessor.
        mode: ``"replace"`` (the default) removes the rows the incoming set
            leaves out, ``"merge"`` upserts only.
        field_map: Forwarded to the row's own ``create_from_input`` /
            ``update_from_input`` call.
        exclude_fields: Forwarded likewise.
        m2m: Forwarded likewise.
        children: Forwarded likewise.
        relations: Forwarded likewise.
        create_service: Optional service replacing that call, with the contract
            :class:`~rest_framework_services.ChildSpec` states; its ``data``
            already carries both link columns. Declaring it alongside the
            row-shaping fields above raises at construction.
        update_service: The same; returning ``None`` means "use the in-memory
            instance".
        delete_service: Replaces the unlink-or-delete rule below, so the outcome
            is reported as ``"removed"`` and an explicit ``orphan`` beside it
            raises.
        orphan: What removing a row *does* —
            :class:`~rest_framework_services.ChildSpec`'s rule applied to the
            pair of link columns rather than to one, since half a link is not a
            state the relation has a meaning for. ``"auto"`` (the default)
            **unlinks** (both columns set to ``None``) when both are nullable
            and **deletes** otherwise; ``"unlink"`` and ``"delete"`` state it
            instead of deriving it, and ``"unlink"`` raises at write time unless
            both columns can hold ``NULL``. The rule also governs the
            :func:`~rest_framework_services.delete_model` cascade.
        error_name: The name this relation reports under when a nested write is
            refused, defaulting to the map key. Set it when the client calls the
            relation something else: a serializer aliasing a nested field
            (``writer = AuthorSerializer(source="author")``) hands the helpers a
            ``validated_data`` keyed by ``source``, so the relation has to be
            declared as ``"author"`` and an error under that name names a field
            the request never had. It renames nothing else -- the payload is
            still read from the map key, and the change carriers still label it
            with the map key, which is the name the spec's author knows it by.
    """

    write_phase: ClassVar[RelationPhase] = RelationPhase.GENERIC

    model: type[Model]
    content_type_field: str = "content_type"
    object_id_field: str = "object_id"
    match_key: str = "pk"
    mode: RelationMode | str = RelationMode.REPLACE
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

    # Declared last, for the reason given above ``orphan``.
    error_name: str | None = None

    def __post_init__(self) -> None:
        validate_relation_mode(self.mode, label="GenericRelationSpec")
        validate_relation_orphan(
            self.orphan, delete_service=self.delete_service, label="GenericRelationSpec"
        )
        validate_relation_services(
            label="GenericRelationSpec",
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


__all__ = ["GenericRelationSpec"]
