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

    One spec covers both: ``OneToOneField`` subclasses ``ForeignKey``, and the
    column being unique changes nothing about how it is written.

    Declared in ``relations={field_name: ForwardRelationSpec(...)}`` on the
    mutation helpers, where the name is the parent's own field
    (``relations={"author": ...}`` for ``Post.author``). The nested payload is
    read from ``data[field_name]`` and the resolved row is assigned to that
    field **before** the parent is saved
    (:attr:`~rest_framework_services.RelationPhase.FORWARD`) — an ordinary
    column assignment, reported by ``diff_attrs`` and persisted by the same
    minimal ``update_fields`` save as any other field.

    The value reads three ways. **Omitted** leaves the relation untouched.
    **``None``** sets the parent's foreign key to ``None`` without removing the
    row it used to point at — a forward target is not owned by the parent and
    may be shared, so removing rows is the reverse kinds' job. **A mapping**
    writes the target row: without a ``match_key`` it creates one, with a
    ``match_key`` it names one, matched against ``scope``.

    A spec writes a *row*. To merely point the column at a row that already
    exists, don't declare a relation at all — pass the pk or the instance as
    the plain field it is.

    There is no ``mode`` and no ``delete_service``: a forward relation has no
    collection to reconcile and no orphans to dispose of. Clearing it is the
    ``None`` case, and it clears the *column*.

    Attributes:
        model: The target model class.
        match_key: The field pairing an incoming payload with an existing row
            (default ``"pk"``), read off both the mapping (``item[match_key]``)
            and the queryset (``filter(**{match_key: key})``). It *identifies* a
            row rather than describing one, so a key matching nothing in
            ``scope`` raises
            :exc:`~rest_framework_services.ServiceValidationError` (a 400)
            instead of falling through to a create — unlike
            :class:`~rest_framework_services.ChildSpec`, which matches inside
            the parent's own manager, where a miss really does mean "a new
            child".
        scope: The rows this caller may update — a queryset, or a callable
            resolved from the caller's ``context`` pool by signature
            (``lambda user: Author.objects.filter(owner=user)``), the library's
            usual idiom. **Without it the spec is create-only**, and a payload
            carrying a ``match_key`` raises
            :exc:`~django.core.exceptions.ImproperlyConfigured` rather than
            quietly creating a duplicate: a forward target has no owning manager
            to match within, so an unscoped by-key match would mean "any caller
            may write any row of that model by guessing a key".
        field_map: Forwarded to the target row's own ``create_from_input`` /
            ``update_from_input`` call, exactly as for the parent.
        exclude_fields: Forwarded likewise.
        m2m: Forwarded likewise — the target's own many-to-many assignments.
        children: Forwarded likewise.
        relations: Forwarded likewise.
        create_service: Optional service replacing that call, for a target whose
            write has behaviour of its own. It receives ``data`` plus the caller
            context — but **no ``parent``**: a forward target is written before
            the parent row exists, which is the whole point of the phase.
            Declaring it alongside the row-shaping fields above raises at
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

    # Declared last, so adding it does not renumber the positional arguments of
    # a spec class that shipped.
    error_name: str | None = None

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
