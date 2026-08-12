"""``ForwardRelationSpec`` — writing the row a parent's own FK column points at."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from django.db.models import Model, QuerySet

from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.relation_phase import RelationPhase
from rest_framework_services.types.relation_spec import RelationSpec
from rest_framework_services.types.utils import validate_relation_services


@dataclass(frozen=True)
class ForwardRelationSpec(RelationSpec):
    """How to persist a **forward** relation — a ``ForeignKey`` *or* a
    ``OneToOneField`` declared on the parent itself.

    One spec for both because Django makes them one thing: ``OneToOneField``
    subclasses ``ForeignKey``, and the column being unique changes nothing
    about how it is written.

    Declared in ``relations={field_name: ForwardRelationSpec(...)}`` on the
    mutation helpers, where the name is the parent's own field
    (``relations={"author": ...}`` for ``Post.author``). The nested payload is
    read from ``data[field_name]`` and the resolved row is assigned to that
    field **before** the parent is saved
    (:attr:`~rest_framework_services.RelationPhase.FORWARD`) — which is what
    makes it an ordinary column assignment, reported by ``diff_attrs`` and
    persisted by the same minimal ``update_fields`` save as any other field,
    with no plumbing of its own.

    The value is read three ways, and all three are meaningful:

    - **omitted** — the relation is untouched.
    - **``None``** — the parent's foreign key is set to ``None``. The row it
      used to point at is *not* removed: a forward target is not owned by the
      parent and may be shared. Removing rows is the reverse kinds' job.
    - **a mapping** — the target row is written. A mapping *without* a
      ``match_key`` creates a row; one *with* it names a row, and is matched
      against ``scope``.

    The ``match_key`` identifies a row rather than describing one, so a key
    that matches nothing in scope raises
    :exc:`~rest_framework_services.ServiceValidationError` (a 400) instead of
    falling through to a create. Creating there would be unsafe as well as
    surprising: the key travels in the payload, so a ``pk`` naming an
    out-of-scope row would be written straight back onto that row by
    ``Model.save()`` — reaching the very row the scope exists to protect. This
    is where a forward relation deliberately parts company with
    :class:`~rest_framework_services.ChildSpec`, whose unmatched rows *are*
    created: a child collection matches within the parent's own manager, so a
    miss there really does mean "a new child".

    A spec writes a *row*. To merely point the column at a row that already
    exists, don't declare a relation at all — pass the pk or the instance as
    the plain field it is.

    Fields:

    - **``model``** — the target model class.
    - **``match_key``** — the field pairing an incoming payload with an
      existing row (default ``"pk"``), read off both the mapping
      (``item[match_key]``) and the queryset (``filter(**{match_key: key})``).
    - **``scope``** — the rows this caller may update: a queryset, or a
      callable resolved from the caller's ``context`` pool by signature
      (``lambda user: Author.objects.filter(owner=user)``), the library's usual
      idiom. **Without it the spec is create-only**, and a payload that
      carries a ``match_key`` raises
      :exc:`~django.core.exceptions.ImproperlyConfigured` rather than quietly
      creating a duplicate. A forward target has no owning manager to scope it
      the way a child collection has, so an unscoped by-key match would mean
      "any caller may write any row of that model by guessing a key" — and the
      remedy is always to declare a scope, never for the client to send
      something else, which is why it is a misconfiguration and not a
      validation error.
    - **``field_map``** / **``exclude_fields``** / **``m2m``** /
     **``children``** / **``relations``** — forwarded to the target row's own
      ``create_from_input`` / ``update_from_input`` call, exactly as for the
      parent.
    - **``create_service``** / **``update_service``** — optional services
      replacing that call, for a target whose write has behaviour of its own.
      They receive ``data`` (and ``instance``, on update) plus the caller
      context — but **no ``parent``**: a forward target is written before
      the parent row exists, which is the whole point of the phase. Declaring
      either alongside the row-shaping knobs above raises at construction, for
      the reason given on :class:`~rest_framework_services.ChildSpec`.

    There is no ``mode`` and no ``delete_service``: a forward relation has no
    collection to reconcile and no orphans to dispose of. Clearing it is the
    ``None`` case, and it clears the *column*.
    """

    write_phase: ClassVar[RelationPhase] = RelationPhase.FORWARD

    model: type[Model]
    match_key: str = "pk"
    scope: QuerySet[Any] | Callable[..., QuerySet[Any]] | None = None
    field_map: dict[str, str] | None = None
    exclude_fields: list[str] | None = None
    m2m: Callable[[Any], Mapping[str, Any]] | None = None
    children: Mapping[str, ChildSpec] | None = None
    relations: Mapping[str, RelationSpec] | None = None
    create_service: Callable[..., Any] | None = None
    update_service: Callable[..., Any] | None = None

    def __post_init__(self) -> None:
        validate_relation_services(
            label="ForwardRelationSpec",
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


__all__ = ["ForwardRelationSpec"]
