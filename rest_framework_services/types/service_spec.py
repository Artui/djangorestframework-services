"""``ServiceSpec`` — bundles per-action configuration for mutation actions."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.utils import is_row_condition, validate_metadata

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT")
ExtraT = TypeVar("ExtraT", bound=Mapping[str, object])

# The default ``many_argument``: the field name the documented workaround gives the
# list (``items = ItemSerializer(many=True)``), so a spec moving from the workaround
# to ``many=True`` keeps the argument a caller sends.
_DEFAULT_MANY_ARGUMENT = "items"
# ASCII rather than ``str.isidentifier``, which admits any Unicode letter and so two
# names that render identically -- a precomposed accent and a letter followed by a
# combining one both pass -- while the JSON key a caller sends back compares them as
# different strings.
_MANY_ARGUMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


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
        idempotent: Whether repeating the call with the same arguments leaves
            the same state as making it once. Declaration-only: nothing in this
            package reads it, because idempotency is a property of the service
            the author writes, not something a dispatcher can arrange. It is
            here so the fact is stated once, on the spec, and every transport
            reads the same answer — a retry policy, a queue's redelivery
            handling, an agent tool annotation. ``None`` means **undeclared**
            and is the default: a transport that turns the signal into a
            published annotation must be able to tell "nothing was said" from a
            declared ``False``, or every spec ever written starts claiming it is
            not idempotent. Note that ``atomic`` is a different question — it
            says a single call is all-or-nothing, not that a second call is a
            no-op.
        partial: Override the partial-validation flag the calling surface
            derives (``False`` for PUT/POST, ``True`` for PATCH). Forcing
            ``False`` on a ``partial_update`` entry makes a PATCH endpoint
            enforce ``required`` like a PUT. Applied once, in
            ``dispatch_mutation_for_spec``, so the viewset mixins, the
            standalone views and ``@service_action`` all honour it.
        many: Validate the request body as a list and render the result list
            the same way. The service receives the validated list as ``data``
            and loops itself, so one call does the batch.
        many_argument: The one argument a ``many=True`` list travels under for a
            caller whose input is always an object of named arguments, and so
            can never be a bare array. ``"items"`` by default. HTTP never reads
            it: the request body stays the array itself. A caller that does
            passes ``many_as_argument=True`` to
            [`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec],
            which reads the list out of that argument and keys every validation
            error under it, and
            [`spec_to_json_schema`][rest_framework_services.jsonschema.spec_to_json_schema.spec_to_json_schema]
            describes the input as an object with that one array property. Must
            be an ASCII identifier. Declaring a name other than the default
            without ``many=True`` raises ``ImproperlyConfigured``: nothing would
            read it.
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
            ``output_serializer`` / ``output_serializer_context`` are ignored,
            and the nested ``permission_classes`` / ``preconditions`` are
            *refused* at ``as_view()`` — the dispatching spec's permissions are
            the ones checked, so declaring them here would guard nothing.
        collection_selector_spec: The LIST-kind twin of
            ``instance_selector_spec``. Its resolved set is seeded into the pool
            as ``collection`` to ``.delete()`` / ``.update()`` / iterate, for an
            instance-less "operate on the filtered set" action where an empty
            set is a harmless no-op rather than a 404. The pool carries query
            params, body and URL kwargs, so a nested-route bulk can scope by
            ``parent_pk``; route captures win on conflict, so a filter value
            cannot override the route scope. Its ``permission_classes`` /
            ``preconditions`` are refused at ``as_view()`` for the same reason as
            ``instance_selector_spec``'s.
        output_selector_spec: The output pipeline as one nested spec. Its
            ``kind`` declares response cardinality: RETRIEVE re-fetches a single
            instance (the service returns the written row, the selector
            re-fetches it with the relations the response needs, and
            ``output_serializer`` renders it); LIST re-fetches and renders a set
            and is valid only alongside ``collection_selector_spec``. ``None``
            renders the service's return value directly. The nested ``kwargs``
            is ignored — the surrounding mutation's chains apply — and the nested
            ``permission_classes`` / ``preconditions`` are refused at
            ``as_view()`` rather than silently ignored.
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
        affordances: What must be true for this operation to be possible right
            now, as a sequence of
            [`Affordance`][rest_framework_services.types.affordance.Affordance]
            declarations. Checked in declaration order after the target guard and
            input validation and **before** ``preconditions``; the first one not
            met raises
            [`ActionUnavailable`][rest_framework_services.exceptions.action_unavailable.ActionUnavailable]
            carrying its ``code``, and the service does not run. Every condition
            on the row is answered by **one** query, however many are declared;
            a condition that is a callable runs off the event loop on the async
            path, as a precondition does. A condition on the row needs a single
            resolved row, so it is refused alongside ``many=True`` or a
            ``collection_selector_spec``, and a dispatch that resolved none
            raises ``ImproperlyConfigured``. It is a check at the moment of the
            call, not a lock: the service still re-validates whatever it relies
            on. ``None`` declares nothing and costs nothing -- no query, no call.
            Codes must be unique within a spec, because the code is how a reader
            tells the conditions apart.
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
        metadata: Consumer-owned and framework-opaque, with **exactly one
            reserved key**: ``"json_schema"``, which
            [`spec_to_json_schema`][rest_framework_services.jsonschema.spec_to_json_schema.spec_to_json_schema]
            merges onto the schema it derives — see that function for the shape,
            the phase keys and the precedence. Every other key is carried and
            never read: no defaulting, no per-key validation, no effect on the
            generated JSON Schema or OpenAPI. Validation at construction stays
            shape-only — a non-``Mapping`` raises ``ImproperlyConfigured`` there,
            and the reserved key is checked when a schema is generated rather
            than here, so declaring metadata never costs a spec that generates
            no schemas.
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
    # Naming (CLAUDE.md rule, third output — a genuinely new field). Bare
    # adjective, to sit with the other per-action flags this dataclass already
    # carries in that shape (``atomic``, ``partial``, ``many``). Not
    # ``idempotent_hint``: that is one transport's spelling, and a spec that
    # named its consumers would have to grow a second field the day a second
    # transport wanted the same fact under its own word — the fact is about the
    # operation, not about the annotation somebody derives from it. Not
    # ``safe``: RFC 9110 reserves that for "no side effects at all", which a
    # mutation is never, so the two words are not interchangeable. ``bool |
    # None`` rather than ``bool`` for the reason the docstring gives — silence
    # must not read as a claim.
    idempotent: bool | None = None
    partial: bool | None = None
    many: bool = False
    # Naming (CLAUDE.md rule, third output — a genuinely new field). Prefixed
    # ``many_`` because it is meaningless without ``many`` and reads as part of
    # that declaration. ``argument`` because it names the one thing a caller
    # passing named arguments sends -- the word ``ArgumentBinding`` and
    # ``UnknownArguments`` already use for client input -- and because an HTTP
    # body is not arguments, so the name does not suggest the body changed. Not
    # ``many_field``, the runner-up: it mirrors the workaround's serializer field
    # exactly, but reads as a field of the request body, which HTTP keeps a bare
    # array. Not ``many_key``: in a bulk write that reads as the key identifying
    # each row. A plain ``str`` defaulting to the name rather than ``None``
    # meaning it, so every reader sees the value in force and no transport
    # re-states the default.
    many_argument: str = _DEFAULT_MANY_ARGUMENT
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
    # Naming (CLAUDE.md rule, third output -- a genuinely new field). Not
    # ``availability``: that names the answer rather than the declaration, and in
    # Django reads as scheduling. Not ``conditions``: ``django-fsm``'s word for
    # the same idea on a transition, which would promise a state machine this is
    # not. ``affordances`` is the established name for the state-dependent set of
    # things a resource currently offers, and it names the object's side of the
    # question -- which is also the key a list reports it under.
    affordances: Sequence[Affordance] | None = None
    response_finalizer: Callable[..., Response | None] | None = None
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        validate_metadata(self.metadata, label="ServiceSpec")
        _validate_many_argument(self)
        _validate_affordances(self)


def _validate_many_argument(spec: ServiceSpec[Any, Any, Any]) -> None:
    """Refuse a ``many_argument`` no caller could send, or that nothing would read.

    At construction, like ``affordances``: the name is only read by callers that never
    mount the spec on a view, so ``as_view()`` would never see it.

    The single-item refusal names a declaration **other than the default** rather
    than any declaration. The field carries its default on every spec, so "declared"
    and "left alone" are the same value; telling them apart would take a ``None``
    default that every reader then resolves to ``"items"`` itself -- the drift one
    declaration exists to prevent. What goes unrefused is writing the default out by
    hand on a single-item spec, which states what that spec already carries.
    """
    name: Any = spec.many_argument
    # Both halves are needed: the pattern raises ``TypeError`` on a non-string rather
    # than refusing it. ``fullmatch``, not ``match``, or a valid prefix passes. Held by
    # test_a_non_string_is_refused_with_the_same_refusal (the type half) and
    # test_a_name_that_is_not_an_ascii_identifier_is_refused (the pattern half).
    if not isinstance(name, str) or _MANY_ARGUMENT_NAME.fullmatch(name) is None:
        raise ImproperlyConfigured(
            f"ServiceSpec.many_argument must be an ASCII identifier (a letter or "
            f"underscore, then letters, digits or underscores); got {name!r}. It is the "
            "name a caller passing named arguments sends the list under."
        )
    # Held by test_a_declaration_on_a_list_payload_is_carried_verbatim (the ``many``
    # half) and test_the_default_on_a_single_item_spec_is_not_a_declaration (the
    # default half); test_a_declared_name_on_a_single_item_spec_is_refused holds both
    # together.
    if not spec.many and name != _DEFAULT_MANY_ARGUMENT:
        raise ImproperlyConfigured(
            f"ServiceSpec declares many_argument={name!r} without many=True. It names "
            "the argument a list travels under, and a single-item spec takes no list; "
            "set many=True or remove it."
        )


def _validate_affordances(spec: ServiceSpec[Any, Any, Any]) -> None:
    """Refuse an ``affordances`` declaration that could never be honoured.

    At construction rather than at ``as_view()``: a spec reaches transports that
    never mount it on a view, and each of these would otherwise surface as a
    request-time error on exactly those transports.
    """
    affordances: Any = spec.affordances
    if affordances is None:
        return
    # ``Sequence`` alone refuses a single ``Affordance`` too -- a dataclass is not
    # one -- as well as a set, whose order would decide which refusal a caller
    # sees, and a generator, which the first dispatch would exhaust.
    if not isinstance(affordances, Sequence):
        raise ImproperlyConfigured(
            "ServiceSpec.affordances takes a sequence of Affordance declarations; got "
            f"{type(affordances).__name__}. Wrap a single one in a list: affordances=[...]."
        )
    codes: set[str] = set()
    for index, affordance in enumerate(affordances):
        if not isinstance(affordance, Affordance):
            raise ImproperlyConfigured(
                f"ServiceSpec.affordances[{index}] must be an Affordance; got "
                f"{type(affordance).__name__}."
            )
        if affordance.code in codes:
            raise ImproperlyConfigured(
                f"ServiceSpec.affordances declares the code {affordance.code!r} twice. A "
                "code is how a client tells one refusal from another, so each must be "
                "unique within a spec."
            )
        codes.add(affordance.code)
        if is_row_condition(affordance.when) and (
            spec.many or spec.collection_selector_spec is not None
        ):
            raise ImproperlyConfigured(
                f"ServiceSpec.affordances[{index}] ({affordance.code!r}) is a condition on "
                "the row, and this spec operates on a set (many=True or a "
                "collection_selector_spec) with no single row to evaluate it against. "
                "Declare it on the per-row operation, or express a rule about the set in "
                "`preconditions`."
            )
