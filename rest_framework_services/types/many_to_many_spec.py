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
    the other model (``Tag.posts``). Django hands back the same related manager
    either way, so forward and reverse are one code path here.

    Each row in ``data[accessor_name]`` is a **payload**, not a key: the target
    row is created or updated first, and only then is the membership written
    (:attr:`~rest_framework_services.RelationPhase.M2M`, after the parent has a
    primary key). That is the difference from the mutation helpers' ``m2m=``
    argument, which assigns rows that already exist and creates nothing. Both
    still ship, because they are different jobs — but a relation named by both
    is refused, since it would be written twice and keep whichever ran last.

    - **omitted** — the relation is untouched.
    - **``[]``** — in ``"replace"`` mode the membership is emptied.
    - **a list of mappings** — every target is written, then the membership is
      set (or added to, in ``"merge"`` mode).

    Fields:

    - **``model``** — the target model class.
    - **``match_key``** — the field pairing an incoming payload with an
      existing target row (default ``"pk"``).
    - **``scope``** — the rows this caller may update: a queryset, or a
      callable resolved from the caller's ``context`` pool by signature.
      **Without it the spec is create-only**, and a payload carrying a
      ``match_key`` raises
      :exc:`~django.core.exceptions.ImproperlyConfigured`. A many-to-many
      target is shared by definition — every parent linked to it reaches the
      same row — so there is no owning manager to match within and an unscoped
      by-key match would mean "any caller may write any row of that model by
      guessing a key". Matching is done in ``scope``, not in the parent's
      current membership: the payload names rows to link, which is exactly the
      set that is not linked yet.
    - **``mode``** — ``"replace"`` (the default) makes the incoming set
      authoritative and drops the members it leaves out; ``"merge"`` adds and
      never drops.
    - **``field_map``** / **``exclude_fields``** / **``m2m``** /
      **``children``** / **``relations``** — forwarded to the *target row's*
      own ``create_from_input`` / ``update_from_input`` call. (``m2m`` here is
      the target's own many-to-many assignments, not this relation.)
    - **``create_service``** / **``update_service``** — optional services
      replacing that call for a target whose write has behaviour of its own.
      They receive ``data`` (and ``instance``, on update) plus ``parent`` and
      the caller context. Unlike a child collection, ``data`` carries no link
      to the parent: the link is a through-table row the loop writes afterwards
      and a service could not write it by returning one. Declaring either
      alongside the row-shaping knobs above raises at construction, for the
      reason given on :class:`~rest_framework_services.ChildSpec`.

    There is no ``delete_service``: a target dropped from the relation is not
    deleted and never was. The row is shared, so the only thing the loop
    removes is the membership — which is why a dropped target is reported
    under :attr:`~rest_framework_services.ChildCollectionChange.unlinked` and
    ``deleted`` stays empty for this kind.

    A many-to-many with an explicit ``through`` model is **not** covered. The
    loop writes target rows and lets Django write the through row, which cannot
    carry the extra columns a custom through model exists for.
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
