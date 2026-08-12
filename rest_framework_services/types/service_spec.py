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

    Used as a value in ``ServiceViewSet.action_specs`` and as the ``spec=``
    argument to :func:`service_action` / :class:`ServiceCreateView` /
    :class:`ServiceUpdateView` / :class:`ServiceDeleteView`.

    Generic parameters are optional and purely informational for type checkers:

    - ``InputT`` — the validated-data type produced by ``input_serializer``.
      For dataclass-based serializers this is the dataclass; for plain
      ``ModelSerializer`` it is typically ``dict[str, Any]``.
    - ``ResultT`` — the value returned by the service callable, and (when
      ``output_selector_spec`` is set) the input to its ``selector``.
    - ``ExtraT`` — a ``TypedDict`` describing the keys returned by ``kwargs``.

    All three default to ``Any``, so ``ServiceSpec(service=fn)`` keeps working
    unchanged.

    Fields are grouped by what they configure: the service callable itself,
    the input pipeline (``input_*``), the output pipeline (a single nested
    :class:`SelectorSpec`), and the cross-cutting concerns (``kwargs``,
    ``permission_classes``).

    ``success_status`` is left as ``None`` so each consumer can supply its
    own action-appropriate default (201 for create, 200 for update, 204
    for destroy). It may also be a **callable** resolved through the framework
    keyword pool — declaring any subset of ``result`` / ``instance`` /
    ``request`` / ``view`` (or ``**kwargs``) — returning the status ``int``.
    This covers upserts whose code depends on the outcome (``200`` for an
    existing row, ``201`` for a freshly created one); the callable sees the
    *service's* return value as ``result``. A ``None`` return from the field
    itself is not meaningful — return an ``int``. OpenAPI can't resolve a
    callable statically, so the generated schema documents the mixin default
    for the dynamic case.

    ``input_data`` is the symmetrical hook for the *serializer's* input.
    Returns a mapping merged on top of ``request.data`` before the
    ``input_serializer`` validates it — useful for lifting URL kwargs
    (e.g. parent IDs from nested routes) into fields the serializer can
    cross-validate. Server-provided keys win on conflict. The provider is
    invoked through the framework's keyword pool, so it declares any subset of
    ``view`` / ``request`` plus ``instance`` (the resolved mutation target,
    ``None`` on create) — or ``**kwargs`` — receiving only what it names, so
    pre-validation input mutation that depends on the current row has a home.
    The same declare-to-receive rule applies to the ``get_input_data`` /
    ``get_<action>_input_data`` view hooks.

    ``input_serializer_context`` is a per-spec hook for the input
    serializer's ``context=`` dict. It sits at the most-specific layer of
    the resolution chain (``view.get_serializer_context`` →
    ``view.get_input_serializer_context`` →
    ``view.get_<action>_input_serializer_context`` → spec hook), so the
    spec wins on overlapping keys. ``None`` (the default) leaves the
    three earlier layers intact. The symmetrical output hook lives on the
    nested ``output_selector_spec.output_serializer_context``; that hook may
    additionally declare a ``result`` keyword to receive the final
    (post-selector) instance being serialized — passed only when declared —
    so it can run a single batched query against it and propagate the
    outcome through context. The output hook always runs *after* the service
    and output selector have resolved ``result``.

    ``output_selector_spec`` is the full output pipeline collapsed into a
    single :class:`SelectorSpec`. Its ``kind`` declares the response
    cardinality: :attr:`SelectorKind.RETRIEVE` (the default) re-fetches a
    single instance (typical pattern: the service returns a freshly
    created/updated instance, the ``output_selector_spec.selector`` re-fetches
    it with the relations the response serializer needs, and the spec's
    ``output_serializer`` renders the result); :attr:`SelectorKind.LIST`
    re-fetches and renders a *set* (``many=True``) and is valid only alongside
    ``collection_selector_spec`` — the bulk-output twin that lets a
    collection mutation return the affected set. ``None`` (the default) means
    "render the service's return value directly". The nested spec's
    ``permission_classes`` and ``kwargs`` are ignored — the surrounding
    mutation's permissions and kwargs chain apply.

    ``instance_selector_spec`` is the input-side twin of
    ``output_selector_spec``: a nested :class:`SelectorSpec` (``kind`` must
    be :attr:`SelectorKind.RETRIEVE`) that resolves the instance an
    update / destroy / detail action targets, embedding the lookup in the
    spec instead of relying on the view's ``queryset`` / ``get_object()``
    chain. The selector's kwarg pool is ``{request, user}`` plus the URL
    kwargs (plus the standard selector extras chain), so
    ``selector=lambda *, pk: Project.objects.filter(pk=pk)`` resolves the
    row from the route. Resolution happens **before** input validation —
    the resolved instance is handed to the input serializer
    (DRF-style ``serializer(instance, data=..., partial=...)``) and seeded
    into the service kwarg pool as ``instance``. A ``None`` / missing
    resolution raises :exc:`~rest_framework.exceptions.NotFound` (the
    nested spec's ``allow_none`` flag is ignored — an update against a
    missing row is always a 404), and object-level permissions
    (``check_object_permissions``) run against the resolved instance. The
    queryset-shaping fields apply; ``permission_classes``,
    ``output_serializer``, and ``output_serializer_context`` on the nested
    spec are ignored. ``None`` (the default) keeps today's ``get_object()``
    chain.

    ``partial`` overrides the transport-derived partial-validation flag.
    ``None`` (the default) inherits the flag the calling surface derives
    (``False`` for PUT/POST, ``True`` for PATCH); ``True`` / ``False``
    forces it regardless of HTTP method — e.g. ``partial=False`` on an
    ``action_specs["partial_update"]`` entry makes a PATCH endpoint
    enforce ``required`` fields like a PUT. Applied once, at
    ``dispatch_mutation_for_spec``, so it is honoured uniformly by the
    viewset mixins, the standalone views, and ``@service_action``.

    ``document_service_error`` is an **OpenAPI-only** flag controlling whether
    the generated schema documents the ``422`` :class:`ServiceError` response
    (it has no effect on runtime behaviour — a service may always raise
    ``ServiceError``). ``None`` (the default) gates the 422 on whether the
    operation validates input (``input_serializer is not None``), so a
    no-input mutation (e.g. a plain delete) doesn't carry a spurious 422 in
    its schema. Set ``True`` to document it anyway (a no-input service that
    *does* raise ``ServiceError``) or ``False`` to drop it (an input-bearing
    service that never raises one). Only consulted when the ``[spectacular]``
    extra is enabled via :func:`~rest_framework_services.openapi.enable_openapi`.

    ``many`` and ``collection_selector_spec`` are the two **bulk** shapes,
    mutually exclusive with each other. ``many=True`` validates the request
    body as a list (``input_serializer`` runs ``many=True``) and renders the
    result list the same way — the service receives the validated list as
    ``data`` and loops itself (``bulk_create`` / a comprehension), so one call
    does the batch. ``collection_selector_spec`` is the LIST-kind twin of
    ``instance_selector_spec``: its resolved set (a queryset or any iterable,
    scoped by the selector + ``filter_set``) is seeded into the pool as
    ``collection`` for the service to ``.delete()`` / ``.update()`` / iterate —
    an instance-less "operate on the filtered set" action (bulk delete/update),
    where an empty set is a harmless no-op. Its selector's kwarg pool carries
    the request's query params and body plus the view's URL kwargs, so a
    nested-route bulk (``/parents/{parent_pk}/children/``) can scope by
    ``parent_pk`` — matching ``instance_selector_spec``. Route captures win over
    client query / body on a key conflict, so a filter value can't override the
    route scope. Both run all-or-nothing under
    ``atomic=True``; authorization is per-set (the view / spec
    ``permission_classes`` plus the scoped selector), with no per-row check.

    ``kwargs`` is a callable that returns extra kwargs to merge into the
    pool the service receives. Co-locating it with the spec lets each action
    declare its own contract — no ``if self.action == ...`` branching in a
    catch-all ``get_service_kwargs``. See :class:`ServiceView` for the
    attributes available on the ``view`` argument.

    ``permission_classes`` overrides the calling view's ``permission_classes``
    for the action the spec backs. ``None`` (the default) means "inherit the
    view's class-level permissions"; an empty sequence means "no permissions"
    explicitly. Forwarded through DRF's ``@action(permission_classes=...)``
    for the ``@service_action`` decorator, and surfaced via ``get_permissions``
    for the viewset mixins and standalone views.

    ``metadata`` is a consumer-owned, framework-opaque mapping — the one field
    here the framework carries but **never reads**. No known keys, no
    per-key validation, no defaulting, no effect on the generated JSON Schema
    or OpenAPI; validation is shape-only, and a non-``Mapping`` raises
    :exc:`~django.core.exceptions.ImproperlyConfigured` at construction. Use
    it to attach a project's own per-operation facts — read back by its own
    permission class, scoping helper, or audit hook — to the spec that
    describes the operation, rather than to a name-keyed side table that
    drifts the day a spec is renamed. It never merges with a nested spec's
    metadata, and it is stored exactly as given (the spec is frozen, the
    mapping is not). See :class:`SelectorSpec` for the full contract.

    ``response_finalizer`` is a post-serialization hook for HTTP response side
    effects (cookies, headers, a swapped response). It runs on the **2xx path
    only**, after the output serializer has produced the ``Response`` and
    *before* it is returned (pre-render); error paths bypass it. Invoked through
    the framework keyword pool, it declares any subset of
    ``response`` / ``result`` / ``request`` / ``view`` / ``instance`` / ``data``
    (or ``**kwargs``) and returns either a ``Response`` (which replaces the
    built one) or ``None`` (which keeps it) — so ``lambda *, response:
    response.set_cookie(...) or response`` attaches a cookie, and returning a
    fresh ``Response`` swaps it wholesale. Unlike the service/selector pool it
    **does** receive ``view`` (a documented exception — a response decision
    legitimately needs view/request context). ``result`` is the *service's*
    return value (the flags carrier), so the idiomatic pattern keeps services
    DRF-free: the service returns domain flags on its result DTO and the
    finalizer translates flags → transport effects. **HTTP-only:** it is skipped
    on the transport-neutral path (``dispatch_spec`` / ``call_service`` / MCP),
    which builds no ``Response``. On the bulk path ``instance`` / ``data`` are
    absent and ``result`` is the dispatched value (post-output-selector).
    """

    # The service callable and per-call dispatch flags.
    service: Callable[..., ResultT]
    atomic: bool = True
    # An ``int`` is used verbatim; a callable is resolved through the framework
    # keyword pool (``result`` / ``instance`` / ``request`` / ``view``) and
    # returns the status — e.g. an upsert returning 200 vs 201; ``None`` lets
    # each consumer apply its action-appropriate default.
    success_status: int | Callable[..., int] | None = None
    partial: bool | None = None
    # Bulk list-payload: validate the request body as a list (``many=True``)
    # and render the result list the same way. The service receives the
    # validated list as ``data`` and loops itself (``bulk_create`` / a
    # comprehension). Mutually exclusive with ``collection_selector_spec``;
    # all-or-nothing under ``atomic=True``.
    many: bool = False
    # OpenAPI-only: whether the generated schema documents the 422
    # ``ServiceError`` response. ``None`` gates it on ``input_serializer is not
    # None``; ``True`` / ``False`` force it. Runtime behaviour is unaffected.
    document_service_error: bool | None = None

    # Input pipeline.
    input_serializer: type | None = None
    # Providers are invoked through the framework's keyword pool: ``input_data``
    # declares any subset of ``view`` / ``request`` plus ``instance`` (the
    # resolved mutation target, ``None`` on create); the context provider
    # declares any subset of ``view`` / ``request`` — or ``**kwargs``. Hence
    # the open ``...`` parameter specs.
    input_data: Callable[..., Mapping[str, Any]] | None = None
    input_serializer_context: Callable[..., Mapping[str, Any]] | None = None
    # Instance resolution for update / destroy / detail actions — the
    # input-side twin of ``output_selector_spec`` (kind=RETRIEVE).
    instance_selector_spec: SelectorSpec[Any, Any] | None = None
    # Collection (bulk) target — a LIST-kind nested spec whose resolved value
    # (a queryset or any iterable) is seeded into the service pool as
    # ``collection`` (for ``collection.delete()`` / ``.update()`` / a loop).
    # The collection-target twin of ``instance_selector_spec``; reuses
    # ``filter_set`` to scope the set. An empty set is a no-op, not a 404.
    # Mutually exclusive with ``many``.
    collection_selector_spec: SelectorSpec[Any, Any] | None = None

    # Output pipeline — the full re-fetch / serialize / shape group lives
    # inside a nested SelectorSpec (kind=RETRIEVE).
    output_selector_spec: SelectorSpec[Any, Any] | None = None

    # Cross-cutting. ``kwargs`` is invoked through the framework's keyword
    # pool, so it declares any subset of ``view`` / ``request`` (or ``**kwargs``)
    # — hence the open ``...`` parameter spec.
    kwargs: Callable[..., ExtraT] | None = None
    permission_classes: Sequence[type[BasePermission]] | None = None
    # Transport-independent progress sink, resolved by the dispatch core and
    # fanned together with whatever reporter the transport supplied (see
    # ``combine_progress``). A **provider**, invoked through the keyword pool
    # like ``kwargs``, returning a ``ProgressReporter`` or ``None``.
    #
    # **Provider, not a bare reporter, and the reason is that the two cannot
    # be told apart.** A ``ProgressReporter`` is a plain callable and so is a
    # factory for one, so a ``reporter | factory`` union would have to *guess*
    # by signature. Every other static-or-callable field in this package
    # (``m2m``, ``success_status``) unions two shapes a type check separates;
    # this one does not, so it takes the useful shape. A static sink is one
    # lambda: ``progress_reporter=lambda: my_sink``.
    #
    # **For sinks that do not care which transport is carrying the run** — a
    # task record, an audit trail, metrics. A sink that needs to know the
    # transport is by definition not transport-independent and belongs on the
    # transport instead (the ``get_progress_reporter`` view hook, or whatever
    # the transport passes to ``dispatch_spec``). Nothing in the pool names the
    # transport, deliberately: that is the seam that would let spec-level
    # behaviour fork per transport.
    progress_reporter: Callable[..., Any] | None = None
    # State/DB business rules, invoked through the keyword pool immediately
    # before the service — after validation and target resolution, so a
    # precondition sees ``data`` / ``serializer`` alongside ``instance`` or
    # ``collection`` / ``user`` / ``request``. Raise-to-abort: the return value
    # is ignored, so a predicate returning ``False`` does nothing. Raise
    # ``ServiceError`` (or ``ServiceValidationError``) — every transport maps
    # those; a DRF ``APIException`` is mapped on HTTP only and escapes the MCP
    # and toolset error contracts.
    #
    # Naming (CLAUDE.md rule, third output — a genuinely new field): not
    # ``guards``, because ``TargetGuard`` / ``on_target_resolved`` already owns
    # "guard" here for an adjacent-but-different contract (caller-supplied
    # authz, singular, not pool-bound); not ``validators``, which collides
    # head-on with ``Serializer.validators`` / ``Field.validators`` carrying
    # different semantics. ``preconditions`` also carries the 409-not-403
    # reading without documentation.
    preconditions: Sequence[Callable[..., None]] | None = None
    # Post-serialization HTTP hook (2xx only, pre-render): resolved through the
    # keyword pool (``response`` / ``result`` / ``request`` / ``view`` /
    # ``instance`` / ``data``), returns a ``Response`` to replace the built one
    # or ``None`` to keep it. HTTP-only — skipped on the transport-neutral path.
    response_finalizer: Callable[..., Response | None] | None = None
    # Consumer-owned, framework-opaque; see the ``SelectorSpec`` field for the
    # naming rationale and the full contract.
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        validate_metadata(self.metadata, label="ServiceSpec")
