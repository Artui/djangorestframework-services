"""``ManyToManySpec`` — writing a many-to-many's target rows from the payload."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from django.db.models import Model, QuerySet

from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.relation_mode import RelationMode
from rest_framework_services.types.relation_phase import RelationPhase
from rest_framework_services.types.relation_spec import RelationSpec
from rest_framework_services.types.utils import validate_relation_mode, validate_relation_services


@dataclass(frozen=True)
class ManyToManySpec(RelationSpec):
    """How to persist a **many-to-many** from nested rows rather than from keys.

    Declared in ``relations={accessor_name: ManyToManySpec(...)}``, where the
    name is whichever side the parent reaches the relation through — the field
    it declares (``Post.tags``) or the reverse accessor of a field declared on
    the other model (``Tag.posts``). Either works; Django hands back the same
    related manager.

    Each row in ``data[accessor_name]`` is a **payload**, not a key: the target
    row is created or updated first, and only then is the membership written
    (:attr:`~rest_framework_services.RelationPhase.M2M`, after the parent has a
    primary key). That is the difference from the mutation helpers' ``m2m=``
    argument, which assigns rows that already exist and creates nothing. A
    relation named by both is refused: it would be written twice and keep
    whichever ran last.

    An omitted relation is untouched; an explicit ``[]`` empties the membership
    in ``"replace"`` mode; a list of mappings writes every target and then sets
    the membership (or adds to it, in ``"merge"`` mode).

    There is no ``delete_service``: the row is shared, so a target dropped from
    the relation loses only its membership and is reported under
    :attr:`~rest_framework_services.ChildCollectionChange.unlinked`, leaving
    ``deleted`` empty for this kind.

    A many-to-many with an explicit ``through`` model is **not** covered. The
    loop writes target rows and lets Django write the through row, which cannot
    carry the extra columns a custom through model exists for.

    Attributes:
        model: The target model class.
        match_key: The field pairing an incoming payload with an existing target
            row (default ``"pk"``).
        scope: The rows this caller may update, on the terms
            :class:`~rest_framework_services.ForwardRelationSpec` states:
            without it the spec is create-only, and a payload carrying a
            ``match_key`` raises
            :exc:`~django.core.exceptions.ImproperlyConfigured`. Matching runs
            against ``scope``, not against the parent's current membership — the
            payload names rows to link, which is exactly the set that is not
            linked yet.
        mode: ``"replace"`` (the default) makes the incoming set authoritative
            and drops the members it leaves out; ``"merge"`` adds and never
            drops.
        field_map: Forwarded to the *target row's* own ``create_from_input`` /
            ``update_from_input`` call.
        exclude_fields: Forwarded likewise.
        m2m: Forwarded likewise — the target's own many-to-many assignments, not
            this relation.
        children: Forwarded likewise.
        relations: Forwarded likewise.
        create_service: Optional service replacing that call for a target whose
            write has behaviour of its own. It receives ``data`` plus ``parent``
            and the caller context, but — unlike a child collection — ``data``
            carries no link to the parent: the link is a through-table row the
            loop writes afterwards, which a service could not write by returning
            one. Declaring it alongside the row-shaping fields above raises at
            construction, for the reason given on
            :class:`~rest_framework_services.ChildSpec`.
        update_service: The same, and additionally receives ``instance``.
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

    write_phase: ClassVar[RelationPhase] = RelationPhase.M2M

    model: type[Model]
    match_key: str = "pk"
    scope: QuerySet[Any] | Callable[..., QuerySet[Any]] | None = None
    mode: RelationMode | str = RelationMode.REPLACE
    field_map: dict[str, str] | None = None
    exclude_fields: list[str] | None = None
    m2m: Callable[[Any], Mapping[str, Any]] | None = None
    children: Mapping[str, ChildSpec] | None = None
    relations: Mapping[str, RelationSpec] | None = None
    create_service: Callable[..., Any] | None = None
    update_service: Callable[..., Any] | None = None

    # Declared last, so adding it does not renumber the positional arguments of
    # a spec class that shipped.
    error_name: str | None = None

    def __post_init__(self) -> None:
        validate_relation_mode(self.mode, label="ManyToManySpec")
        validate_relation_services(
            label="ManyToManySpec",
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


__all__ = ["ManyToManySpec"]
