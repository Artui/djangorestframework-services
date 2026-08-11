"""``GenericRelationSpec`` — rows linked to the parent by a content type."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from django.db.models import Model

from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.relation_mode import RelationMode
from rest_framework_services.types.relation_phase import RelationPhase
from rest_framework_services.types.relation_spec import RelationSpec
from rest_framework_services.types.utils import validate_relation_mode, validate_relation_services


@dataclass(frozen=True)
class GenericRelationSpec(RelationSpec):
    """How to persist a ``GenericRelation`` — a collection linked by content type.

    The reverse-FK collection with the foreign key replaced by a pair of
    columns: a ``ForeignKey`` to ``ContentType`` saying *which model* the row
    belongs to, and an id column saying *which row*. Everything else is the
    same, which is why it reconciles exactly as
    :class:`~rest_framework_services.ChildSpec` does — matched inside the
    parent's own accessor, so no ``scope=`` is needed or accepted — and why it
    is written after the parent's ``save()``
    (:attr:`~rest_framework_services.RelationPhase.GENERIC`, once the parent
    has both a content type and a primary key).

    Declared in ``relations={accessor_name: GenericRelationSpec(...)}``, where
    the name is the ``GenericRelation`` declared on the parent
    (``relations={"attachments": ...}`` for ``Catalog.attachments``). A
    relation the input omits is untouched; an explicit ``[]`` in ``"replace"``
    mode empties it.

    **This kind needs ``django.contrib.contenttypes`` in ``INSTALLED_APPS``**
    and nothing else in the library does, so the content-type lookup is
    resolved on first use rather than imported with the package: this package
    ships in ``INSTALLED_APPS`` itself and is imported while Django is still
    populating the app registry, where importing a model raises
    ``AppRegistryNotReady``. Declaring the spec is always safe; *writing* one
    without the app installed raises
    :exc:`~django.core.exceptions.ImproperlyConfigured` naming the remedy.

    Fields:

    - **``model``** — the related model class (the one carrying the content
      type and id columns, e.g. ``Attachment``).
    - **``content_type_field``** / **``object_id_field``** — the names of those
      two columns, defaulting to Django's own ``"content_type"`` /
      ``"object_id"``. They mirror the arguments of the same name on
      ``GenericRelation``; set them when the model spells them differently.
    - **``match_key``** — the field pairing an incoming row with an existing
      one (default ``"pk"``), read inside the parent's own accessor.
    - **``mode``** — ``"replace"`` (the default) removes the rows the incoming
      set leaves out, ``"merge"`` upserts only. An orphan is **unlinked** (both
      link columns set to ``None``) when both are nullable, and **deleted**
      otherwise — the same rule
      :class:`~rest_framework_services.ChildSpec` applies to one column,
      applied to the pair.
    - **``field_map``** / **``exclude_fields``** / **``m2m``** /
      **``children``** / **``relations``** — forwarded to the row's own
      ``create_from_input`` / ``update_from_input`` call.
    - **``create_service``** / **``update_service``** / **``delete_service``**
      — optional services replacing that call, with the same contract as
      :class:`~rest_framework_services.ChildSpec`'s: a create service's
      ``data`` already carries both link columns, an update service returning
      ``None`` means "use the in-memory instance", and a delete service
      replaces the unlink-or-delete rule (so the outcome is reported as
      ``"removed"``). Declaring ``create_service`` / ``update_service``
      alongside the row-shaping knobs raises at construction.
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

    def __post_init__(self) -> None:
        validate_relation_mode(self.mode, label="GenericRelationSpec")
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
