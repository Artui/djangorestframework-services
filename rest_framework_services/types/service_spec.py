"""``ServiceSpec`` — bundles per-action configuration for mutation actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.utils import validate_metadata

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT")
ExtraT = TypeVar("ExtraT", bound=Mapping[str, object])


@dataclass(frozen=True)
class ServiceSpec(Generic[InputT, ResultT, ExtraT]):
    """All wiring for a single mutation action in one record.

    Used as a value in ``ServiceViewSet.action_specs`` and as the ``spec=`` argument to
    [`service_action`][rest_framework_services.viewsets.decorators.service_action.service_action]
    /
    [`ServiceCreateView`][rest_framework_services.views.mutation.service_create_view.ServiceCreateView]
    /
    [`ServiceUpdateView`][rest_framework_services.views.mutation.service_update_view.ServiceUpdateView]
    /
    [`ServiceDeleteView`][rest_framework_services.views.mutation.service_delete_view.ServiceDeleteView].

    Fields group into the service callable itself, the input pipeline (``input_*``), the
    output pipeline (a nested
    [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec]), and
    cross-cutting concerns. Several are **providers**: they are resolved through the
    framework keyword pool, declaring any subset of the keywords listed for them (or
    ``**kwargs``) and receiving only what they name.

    The generic parameters are optional and purely informational for type
    checkers. ``InputT`` is the validated-data type ``input_serializer``
    produces (the dataclass for dataclass-based serializers, usually
    ``dict[str, Any]`` for a plain ``ModelSerializer``), ``ResultT`` the
    service's return value and the input to ``output_selector_spec.selector``,
    and ``ExtraT`` a ``TypedDict`` of the keys ``kwargs`` returns. All three
    default to ``Any``, so ``ServiceSpec(service=fn)`` keeps working unchanged.

    ``many`` and ``collection_selector_spec`` are the two bulk shapes and are
    mutually exclusive. Both run all-or-nothing under ``atomic=True``, and both
    authorize per-set — the view / spec ``permission_classes`` plus the scoped
    selector, with no per-row check.

    Attributes:
        service: The callable the action runs.
        atomic: Run the dispatch in a transaction.
        success_status: The 2xx status. An ``int`` is used verbatim; a provider
            (pool: ``result`` / ``instance`` / ``request`` / ``view``) returns
            one, which is what an upsert answering 201 or 200 by outcome needs
            — it sees the *service's* return value as ``result``. ``None`` lets
            each consumer apply its action-appropriate default (201 create, 200
            update, 204 destroy). OpenAPI cannot resolve a provider statically,
            so the schema documents the mixin default in that case.
        partial: Override the partial-validation flag the calling surface
            derives (``False`` for PUT/POST, ``True`` for PATCH). Forcing
            ``False`` on a ``partial_update`` entry makes a PATCH endpoint
            enforce ``required`` like a PUT. Applied once, in
            ``dispatch_mutation_for_spec``, so the viewset mixins, the
            standalone views and ``@service_action`` all honour it.
        many: Validate the request body as a list and render the result list
            the same way. The service receives the validated list as ``data``
            and loops itself, so one call does the batch.
        document_service_error: OpenAPI-only — whether the schema documents the
            422 [`ServiceError`][rest_framework_services.exceptions.service_error.ServiceError] response. No runtime effect; a service may
            always raise. ``None`` gates it on ``input_serializer is not None``,
            so a plain delete carries no spurious 422. Only consulted when the
            ``[spectacular]`` extra is enabled through
            [`enable_openapi`][rest_framework_services.openapi.enable_openapi.enable_openapi].
        input_serializer: Validates the request body.
        input_data: Provider (pool: ``view`` / ``request`` / ``instance``,
            the latter ``None`` on create) returning a mapping merged on top of
            ``request.data`` before validation — the home for lifting URL kwargs
            such as a nested route's parent id into fields the serializer can
            cross-validate. Server-provided keys win on conflict. The
            ``get_input_data`` / ``get_<action>_input_data`` view hooks follow
            the same declare-to-receive rule.
        input_serializer_context: Provider for the input serializer's
            ``context=``, at the most specific layer of the chain
            (``get_serializer_context`` → ``get_input_serializer_context`` →
            ``get_<action>_input_serializer_context`` → this), so it wins on
            overlapping keys. ``None`` leaves the earlier layers intact. The
            output twin is ``output_selector_spec.output_serializer_context``,
            which may also declare ``result`` to receive the post-selector
            instance and run a single batched query against it.
        instance_selector_spec: Nested RETRIEVE spec resolving the row an
            update / destroy / detail action targets, embedding the lookup in
            the spec rather than the view's ``queryset`` / ``get_object()``
            chain. Its kwarg pool is ``{request, user}`` plus the URL kwargs, so
            ``selector=lambda *, pk: Project.objects.filter(pk=pk)`` resolves
            from the route. Resolution runs **before** input validation: the row
            is handed to the input serializer DRF-style and seeded into the
            service pool as ``instance``. A missing row is always
            ``NotFound`` — the nested
            ``allow_none`` is ignored — and ``check_object_permissions`` runs
            against it. Queryset shaping applies; the nested
            ``permission_classes`` / ``output_serializer`` /
            ``output_serializer_context`` are ignored.
        collection_selector_spec: The LIST-kind twin of
            ``instance_selector_spec``. Its resolved set is seeded into the pool
            as ``collection`` to ``.delete()`` / ``.update()`` / iterate, for an
            instance-less "operate on the filtered set" action where an empty
            set is a harmless no-op rather than a 404. The pool carries query
            params, body and URL kwargs, so a nested-route bulk can scope by
            ``parent_pk``; route captures win on conflict, so a filter value
            cannot override the route scope.
        output_selector_spec: The output pipeline as one nested spec. Its
            ``kind`` declares response cardinality: RETRIEVE re-fetches a single
            instance (the service returns the written row, the selector
            re-fetches it with the relations the response needs, and
            ``output_serializer`` renders it); LIST re-fetches and renders a set
            and is valid only alongside ``collection_selector_spec``. ``None``
            renders the service's return value directly. The nested
            ``permission_classes`` and ``kwargs`` are ignored — the surrounding
            mutation's chains apply.
        kwargs: Provider (pool: ``view`` / ``request``) of extra kwargs merged
            into the pool the service receives. Co-locating it with the spec
            lets each action declare its own contract, instead of
            ``if self.action == ...`` branching in one catch-all. See
            [`ServiceView`][rest_framework_services.types.service_view.ServiceView] for what the ``view`` argument offers.
        permission_classes: Override the calling view's permissions for this
            action. ``None`` inherits the view's; an empty sequence means none,
            explicitly. Forwarded through DRF's ``@action`` for
            ``@service_action`` and surfaced via ``get_permissions`` elsewhere.
        progress_reporter: Provider returning a [`ProgressReporter`][rest_framework_services.types.progress_reporter.ProgressReporter] sink,
            fanned together with whatever reporter the transport supplied. For
            sinks that do not care which transport carries the run — a task
            record, an audit trail, metrics.
        preconditions: State/DB rules invoked immediately before the service,
            after validation and target resolution, so each sees ``data`` /
            ``serializer`` alongside ``instance`` or ``collection`` / ``user`` /
            ``request``. Raise-to-abort: the return value is ignored, so a
            predicate returning ``False`` does nothing. Raise ``ServiceError``
            or ``ServiceValidationError`` — every transport maps those, whereas
            a DRF ``APIException`` is mapped on HTTP only.
        response_finalizer: Provider (pool: ``response`` / ``result`` /
            ``request`` / ``view`` / ``instance`` / ``data``) for HTTP response
            side effects — cookies, headers, a swapped response. Runs on the
            **2xx path only**, after the output serializer has built the
            ``Response`` and before it is returned; error paths bypass it.
            Return a ``Response`` to replace the built one or ``None`` to keep
            it. ``result`` is the *service's* return value, so the idiomatic
            pattern keeps services DRF-free: the service returns domain flags
            and the finalizer translates them into transport effects.
            **HTTP-only** — skipped on the transport-neutral path, which builds
            no ``Response``. On the bulk path ``instance`` / ``data`` are absent
            and ``result`` is the post-output-selector value.
        metadata: Consumer-owned and framework-opaque — the one field carried
            but **never read**. No known keys, no per-key validation, no
            defaulting, no effect on the generated JSON Schema or OpenAPI;
            validation is shape-only, and a non-``Mapping`` raises
            ``ImproperlyConfigured`` at construction.
            Use it to attach a project's own per-operation facts, read back by
            its own permission class or audit hook, to the spec describing the
            operation rather than to a name-keyed side table that drifts the day
            a spec is renamed. It never merges with a nested spec's metadata and
            is stored exactly as given. See [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec].
    """

    # The service callable and per-call dispatch flags.
    service: Callable[..., ResultT]
    atomic: bool = True
    success_status: int | Callable[..., int] | None = None
    partial: bool | None = None
    many: bool = False
    document_service_error: bool | None = None

    # Input pipeline. The open ``...`` parameter specs are because every
    # provider here is resolved through the keyword pool, so its signature is
    # whichever subset of the documented keywords it declares.
    input_serializer: type | None = None
    input_data: Callable[..., Mapping[str, Any]] | None = None
    input_serializer_context: Callable[..., Mapping[str, Any]] | None = None
    instance_selector_spec: SelectorSpec[Any, Any] | None = None
    collection_selector_spec: SelectorSpec[Any, Any] | None = None

    # Output pipeline.
    output_selector_spec: SelectorSpec[Any, Any] | None = None

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
    # Naming (CLAUDE.md rule, third output — a genuinely new field): not
    # ``guards``, because ``TargetGuard`` / ``on_target_resolved`` already owns
    # "guard" here for an adjacent-but-different contract (caller-supplied
    # authz, singular, not pool-bound); not ``validators``, which collides
    # head-on with ``Serializer.validators`` / ``Field.validators`` carrying
    # different semantics. ``preconditions`` also carries the 409-not-403
    # reading without documentation.
    preconditions: Sequence[Callable[..., None]] | None = None
    response_finalizer: Callable[..., Response | None] | None = None
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        validate_metadata(self.metadata, label="ServiceSpec")
