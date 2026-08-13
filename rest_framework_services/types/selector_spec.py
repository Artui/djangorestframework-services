"""``SelectorSpec`` — bundles per-action configuration for read actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from django.db.models import QuerySet
from django.db.models.query import Prefetch
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.serializers import Serializer

from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.service_view import ServiceView
from rest_framework_services.types.utils import validate_metadata

ResultT = TypeVar("ResultT")
ExtraT = TypeVar("ExtraT", bound=Mapping[str, object])


@dataclass(frozen=True, kw_only=True)
class SelectorSpec(Generic[ResultT, ExtraT]):
    """All wiring for a single read action in one record.

    Used as a value in ``action_specs`` on viewsets, as the ``spec=``
    argument to [`SelectorListView`][rest_framework_services.views.query.selector_list_view.SelectorListView] / [`SelectorRetrieveView`][rest_framework_services.views.query.selector_retrieve_view.SelectorRetrieveView],
    and as the ``output_selector_spec`` field on [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec]
    (where it describes the post-mutation re-fetch).

    All fields are keyword-only: ``SelectorSpec(kind=SelectorKind.LIST,
    selector=fn)`` rather than positional. ``kind`` is required and is the only
    field without a default. Several fields are **providers**: they are
    resolved through the framework keyword pool, declaring any subset of the
    keywords listed for them (or ``**kwargs``) and receiving only what they
    name.

    The generic parameters both default to ``Any``: ``ResultT`` is the
    selector's return type and ``ExtraT`` a ``TypedDict`` of the keys
    ``kwargs`` returns.

    The five shaping fields (``select_related`` / ``prefetch_related`` /
    ``annotations`` / ``extend_queryset`` / ``filter_set``) require
    ``selector`` to be set and the selector to return a Django
    ``QuerySet``. Configuring any of them with no selector raises
    ``ImproperlyConfigured`` at ``as_view()`` time; a non-QuerySet return
    raises at request time.

    Attributes:
        kind: Required [`SelectorKind`][rest_framework_services.types.selector_kind.SelectorKind] discriminator. ``RETRIEVE``
            materializes a QuerySet via ``.first()`` and raises
            ``NotFound`` on a ``None`` /
            missing object; ``LIST`` returns whatever the selector returns
            unchanged. It also drives the fail-fast check that the spec is
            mounted on a compatible view — a ``LIST`` spec on
            [`SelectorRetrieveView`][rest_framework_services.views.query.selector_retrieve_view.SelectorRetrieveView] raises at ``as_view()``. Being
            explicit rather than inferred from the call site, it also carries
            the semantics outside a request, to a management command or any
            other non-DRF caller.
        selector: Callable invoked by ``get_queryset()`` (list) or
            ``get_object()`` (retrieve). ``None`` uses the configured
            ``queryset`` / default DRF behaviour.
        allow_none: RETRIEVE-only knob for the ``None`` / missing-object case.
            ``False`` raises ``NotFound``;
            ``True`` expresses a nullable-resource contract, where the
            standalone retrieve view and the retrieve viewset mixin render
            ``200`` with a JSON ``null`` body and skip the output serializer.
            **Ignored** when the spec is nested:
            ``ServiceSpec.output_selector_spec`` keeps its
            authoritative-``None`` → 204 contract and
            ``ServiceSpec.instance_selector_spec`` always 404s.
        output_serializer: DRF ``Serializer`` subclass used by
            ``get_serializer_class()`` for this action. ``None`` falls back to
            DRF's standard ``serializer_class``.
        output_serializer_context: Provider for the response serializer's
            ``context=``, at the most specific layer of the chain
            (``get_serializer_context`` → ``get_output_serializer_context`` →
            ``get_<action>_output_serializer_context`` → this), so it wins on
            overlapping keys. ``None`` leaves the earlier layers intact. Its
            pool is ``view`` / ``request`` plus the resolved data being
            serialized — ``page`` on a LIST spec (the paginated object list, or
            the full queryset when pagination is off) or ``instance`` on a
            RETRIEVE spec — so it can run a single batched query against the
            exact objects being serialized and propagate the outcome through
            context, as in ``lambda *, page: {"votes": tally(page)}``. It always
            runs *after* the data is resolved. Selectors do not validate input,
            so there is no symmetrical ``input_serializer_context``.
        select_related: Relation names, forwarded as
            ``qs.select_related(*spec.select_related)``.
        prefetch_related: Relation names or ``Prefetch`` objects.
        annotations: Mapping merged into a single ``.annotate(**...)`` call.
            With the two fields above, this is declarative shaping applied to
            the selector's return value before it leaves
            ``dispatch_selector_for_spec`` — reach for it whenever the same
            shaping applies every request, since it stays introspectable.
        extend_queryset: Dynamic escape hatch, invoked *after* the declarative
            fields have applied, so it always sees the fully statically-shaped
            queryset. Use it when the shaping depends on the request, such as
            prefetching only when a query string opts in. Synchronous only — it
            manipulates the queryset's lazy expression tree, not the database.
        filter_set: Transport-neutral filtering applied to the selector's
            QuerySet: a ``django-filter`` ``FilterSet`` class, or any object
            honouring the same ``(data, queryset) -> .qs`` contract. The
            dispatcher calls
            ``filter_set(data=request.query_params, queryset=qs).qs``, so the
            *declaration* of which fields are filterable lives on the spec
            while the *values* come from the request. Applied **after** the
            four shaping fields and **before** the retrieve ``.first()``, so it
            composes with shaping and narrows ``RETRIEVE`` selectors too, where
            ``RetrieveModelMixin`` runs no filter step. On the list path it
            **replaces**
            ``DjangoFilterBackend`` rather
            than stacking with it — the values come off the same
            ``request.query_params`` — and wiring both for one action raises at
            ``as_view()``. Replacing it means keeping its contract, so
            **invalid filter input is rejected with a 400**: the ``FilterSet``
            is validated via ``is_valid()`` and its ``errors`` are raised as a
            DRF ``ValidationError``, where
            reading ``.qs`` unvalidated would answer 200 with *unfiltered* rows
            in django-filter's default non-strict mode. That is enforced only
            when the duck-typed object actually exposes ``is_valid``; a bare
            ``(data, queryset) -> .qs`` stand-in keeps its pass-through
            behaviour. The dispatcher also forwards the ``request`` into the
            ``FilterSet`` when its constructor declares one, so a
            request-scoped ``FilterSet`` — ``self.request.user`` scoping, a
            request-aware ``ModelChoiceFilter`` ``queryset`` — sees the same
            ``self.request`` it would behind ``DjangoFilterBackend`` rather
            than ``None``: real on the HTTP / MCP paths, and a synthetic
            off-HTTP one whose ``user`` and ``query_params`` are faithful
            (headers / session are best-effort there). A bare
            ``(data, queryset)`` stand-in never receives it. ``None`` applies
            no filtering. Reach for it only when the selector returns a
            QuerySet: for an aggregate / computed return the ``?param`` values
            are computation inputs, so use ``kwargs`` /
            ``get_selector_kwargs()`` instead.
        kwargs: Provider (pool: ``view`` / ``request``) of extra kwargs merged
            into the pool the selector receives. Co-locating it with the spec
            lets each action declare its own contract, instead of
            ``if self.action == ...`` branching in one catch-all
            ``get_selector_kwargs``.
        permission_classes: Override the calling view's permissions for the
            action the spec backs. ``None`` inherits the view's class-level
            permissions; an empty sequence means none, explicitly. Forwarded
            through DRF's ``@action(permission_classes=...)`` for the
            ``@selector_action`` decorator and surfaced via ``get_permissions``
            for the viewset mixins and standalone views. Ignored when the spec
            is nested under ``ServiceSpec.output_selector_spec`` — the
            surrounding mutation action's permissions apply.
        progress_reporter: Provider returning a [`ProgressReporter`][rest_framework_services.types.progress_reporter.ProgressReporter] sink,
            fanned together with whatever reporter the transport supplied. For
            sinks that do not care which transport carries the run — a task
            record, an audit trail, metrics.
        preconditions: State/DB rules invoked after the target resolves, seeded
            with ``instance`` (RETRIEVE) or ``collection`` (LIST). A selector
            has no validation step, so that is the one position available, and
            pool binding does the discrimination — a precondition declaring
            ``instance`` cannot be written against a LIST spec. Raise-to-abort:
            the return value is ignored. See [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] for the raise
            contract.
        metadata: Consumer-owned, framework-opaque mapping — **the framework
            never reads it**. No known keys, no per-key validation, no
            defaulting, no effect on the generated JSON Schema or OpenAPI;
            validation is shape-only, and a non-``Mapping`` raises
            ``ImproperlyConfigured`` at
            construction. Use it to attach a project's own per-operation facts
            — read back by its own permission class, scoping helper, or audit
            hook — to the spec describing the operation, rather than to a
            name-keyed side table that drifts the day a spec is renamed. It is
            reachable wherever the spec is:
            ``view.action_specs[view.action].metadata`` inside a permission
            class (which receives ``(request, view)``, so it has the spec but
            knows no registry or name), or ``entry.spec.metadata`` from a
            [`RegisteredSpec`][rest_framework_services.types.registered_spec.RegisteredSpec],
            whose ``tags`` handles the boolean-ish labels this is *not* for. It
            never merges or inherits — a [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] and its
            ``output_selector_spec`` carry independent metadata — and it is
            stored exactly as given: the spec is frozen, the mapping is not,
            and the library neither copies nor deep-freezes it, so pass
            something you don't mutate. ``None`` means "not declared", which
            stays distinguishable from a declared empty mapping.
    """

    kind: SelectorKind

    # The selector callable.
    selector: Callable[..., ResultT] | None = None
    allow_none: bool = False

    # Output pipeline plus the shaping fields applied to the selector's
    # QuerySet return. The open ``...`` parameter specs are because every
    # provider here is resolved through the keyword pool, so its signature is
    # whichever subset of the documented keywords it declares.
    output_serializer: type[Serializer] | None = None
    output_serializer_context: Callable[..., Mapping[str, Any]] | None = None
    select_related: Sequence[str] | None = None
    prefetch_related: Sequence[str | Prefetch] | None = None
    annotations: Mapping[str, Any] | None = None
    extend_queryset: Callable[[QuerySet[Any], ServiceView, Request], QuerySet[Any]] | None = None
    # Typed ``Any`` so ``types/`` never imports django-filter (the
    # dependency-sink rule); services applies it by duck typing.
    filter_set: Any | None = None

    # Cross-cutting.
    kwargs: Callable[..., ExtraT] | None = None
    permission_classes: Sequence[type[BasePermission]] | None = None
    # A provider rather than a bare reporter because the two cannot be told
    # apart: a ``ProgressReporter`` is a plain callable and so is a factory for
    # one, so a ``reporter | factory`` union would have to guess by signature.
    # Every other static-or-callable field here (``m2m``, ``success_status``)
    # unions two shapes a type check separates; this one does not, so it takes
    # the useful shape. A static sink is one lambda.
    #
    # Nothing in the pool names the transport, deliberately — that is the seam
    # that would let spec-level behaviour fork per transport. A sink needing to
    # know the transport belongs on the transport instead.
    progress_reporter: Callable[..., Any] | None = None
    # Naming: see the ``ServiceSpec.preconditions`` field for the rationale.
    preconditions: Sequence[Callable[..., None]] | None = None
    # Consumer-owned, framework-opaque. The name fits none of the three
    # patterns in CLAUDE.md (it wraps no Django/DRF concept, configures no
    # serialization phase, resolves no nested spec), so it is chosen for the
    # convention it does match: ``dataclasses.field(metadata=...)``, whose
    # semantics — a read-only mapping the library carries but never reads —
    # are exactly these. Not ``extras``: that already names the keyword pool
    # selectors and services receive (``**extras: Unpack[...]``).
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        validate_metadata(self.metadata, label="SelectorSpec")
