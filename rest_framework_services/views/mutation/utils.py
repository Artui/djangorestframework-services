"""Helpers used by the mutation views, viewset mixins, and ``@service_action``.

Public leaf helpers:

- ``build_input_serializer`` — construct and validate the bound input
  serializer (instance-aware on update/destroy flows); ``None`` when the
  spec has no ``input_serializer``.
- ``validate_input`` — turn ``request.data`` into the serializer's
  ``validated_data`` (dict for ``ModelSerializer``, dataclass instance for
  dataclass-based serializers). Thin wrapper over
  ``build_input_serializer``.
- ``dispatch_service`` — sync/async dispatch with optional atomic wrapping.
- ``map_service_error`` — translate a framework-agnostic ``ServiceError``
  into the appropriate DRF exception. Lives in the sibling
  :mod:`~rest_framework_services.views.mutation.map_service_error` leaf module
  (re-imported here for the flow runner's use); kept separate so
  ``call_service`` can map errors without importing this heavy module.
- ``resolve_mutation_instance`` — resolve the instance an update / destroy /
  detail action targets: ``spec.instance_selector_spec`` when set, else the
  view's ``get_object()`` chain.

Internal:

- ``_execute_mutation`` — the underlying flow runner. Used by
  :class:`~rest_framework_services.views.mutation.mutation_flow_mixin.MutationFlowMixin`
  (composed into views / per-action mixins) and by ``@service_action``
  (which can't inherit from a mixin because it's a decorator).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import is_dataclass
from typing import Any

from asgiref.sync import async_to_sync
from django.http import QueryDict
from rest_framework import exceptions as drf_exceptions
from rest_framework import status as drf_status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services.dispatch.base_pool import base_pool
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.is_async import is_async
from rest_framework_services.selectors.utils import (
    apply_queryset_shaping,
    dispatch_selector_for_spec,
    is_queryset,
    run_selector,
)
from rest_framework_services.services.arun_service import arun_service
from rest_framework_services.services.run_service import run_service
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.apply_response_finalizer import (
    apply_response_finalizer,
)
from rest_framework_services.views.mutation.map_service_error import (
    map_service_error,
)
from rest_framework_services.views.mutation.resolve_success_status import resolve_success_status
from rest_framework_services.views.utils import (
    resolve_callable_kwargs,
    resolve_extra_kwargs,
    resolve_input_extras,
    resolve_serializer_context,
)


def build_input_serializer(
    request: Request,
    input_serializer: type | None,
    *,
    partial: bool = False,
    extra_data: Mapping[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    instance: Any = None,
    many: bool = False,
) -> Serializer | None:
    """Construct + validate the bound input serializer; ``None`` if absent.

    ``input_serializer`` may be:

    - a bare dataclass type — wrapped in a ``DataclassSerializer`` on the fly;
      ``validated_data`` is a dataclass instance;
    - a ``DataclassSerializer`` subclass — instantiated directly;
      ``validated_data`` is a dataclass instance;
    - any other ``Serializer`` subclass (e.g. ``ModelSerializer``) —
      instantiated directly; ``validated_data`` is a ``dict``.

    ``extra_data`` (when supplied) is merged on top of ``request.data``
    before the serializer instantiates — server-provided keys win on
    overlap. This is the seam used by the ``input_data`` resolver chain
    to lift URL kwargs into serializer input. A form-encoded / multipart
    body arrives as a ``QueryDict`` (``{key: [values]}`` internally), so the
    merge goes through :func:`_merge_extra_data` to avoid flattening scalars
    into one-element lists — see there.

    ``context`` (when supplied) is forwarded to the serializer's ``context=``
    kwarg so DRF-style ``self.context["request"]`` / ``["view"]`` lookups
    work inside validators and fields.

    ``instance`` (when supplied) is the resolved mutation target on
    update / destroy flows. The serializer is constructed DRF-style —
    ``serializer(instance, data=data, partial=partial)`` — so
    ``self.instance`` is populated inside ``validate()`` / field validators
    and instance-aware validators (e.g. ``UniqueValidator`` excluding the
    current row) behave as they do under DRF's own update flow.

    ``many`` (when ``True``) validates ``data`` as a list — the bulk
    list-payload path; ``validated_data`` is then a list of items.

    The serializer is returned validated (``is_valid(raise_exception=True)``
    has run) but never saved; the service owns persistence.
    """
    if input_serializer is None:
        return None
    if extra_data:
        data: Any = _merge_extra_data(request.data, extra_data)
    else:
        data = request.data
    return build_input_serializer_from_data(
        data,
        input_serializer,
        partial=partial,
        context=context,
        instance=instance,
        many=many,
    )


def _merge_extra_data(request_data: Any, extra_data: Mapping[str, Any]) -> Any:
    """Merge server-provided ``extra_data`` on top of a request body.

    A JSON body parses to a plain ``dict`` and merges by unpacking, extras
    winning on overlap. A form-encoded / multipart body, however, is a DRF
    ``QueryDict`` whose internal storage is ``{key: [values]}`` — dict-unpacking
    it (``{**request_data, ...}``) would expose those value *lists*, turning
    every scalar field into a one-element list and breaking validation (a
    ``ChoiceField`` would see ``['X']`` → ``invalid_choice``). Copy the QueryDict
    (``.copy()`` returns a mutable one) and set each extra through its native
    API instead — ``setlist`` for list/tuple values, plain assignment for
    scalars — so scalars stay scalars and multi-value fields keep their lists,
    matching how DRF's own serializers consume a QueryDict.
    """
    if not isinstance(request_data, QueryDict):
        return {**request_data, **extra_data}
    merged = request_data.copy()
    for key, value in extra_data.items():
        if isinstance(value, (list, tuple)):
            merged.setlist(key, list(value))
        else:
            merged[key] = value
    return merged


def build_input_serializer_from_data(
    data: Any,
    input_serializer: type | None,
    *,
    partial: bool = False,
    context: dict[str, Any] | None = None,
    instance: Any = None,
    many: bool = False,
) -> Serializer | None:
    """Construct + validate the bound input serializer from a raw ``data`` dict.

    The transport-neutral core of :func:`build_input_serializer`: it takes the
    input ``data`` directly instead of reaching into a DRF ``request.data``, so
    a non-HTTP caller (``dispatch_spec``) and the HTTP view path share one
    validation implementation. See :func:`build_input_serializer` for the
    ``input_serializer`` / ``partial`` / ``context`` / ``instance`` / ``many``
    semantics.
    """
    if input_serializer is None:
        return None
    serializer_kwargs: dict[str, Any] = {"data": data, "partial": partial}
    if many:
        serializer_kwargs["many"] = True
    if instance is not None:
        serializer_kwargs["instance"] = instance
    if context is not None:
        serializer_kwargs["context"] = context
    if isinstance(input_serializer, type) and issubclass(input_serializer, Serializer):
        serializer: Serializer = input_serializer(**serializer_kwargs)
    elif is_dataclass(input_serializer):
        serializer = DataclassSerializer(
            dataclass=input_serializer,
            **serializer_kwargs,
        )
    else:
        raise TypeError(
            "input_serializer must be a dataclass type or a Serializer subclass; "
            f"got {input_serializer!r}."
        )
    serializer.is_valid(raise_exception=True)
    return serializer


def validate_input(
    request: Request,
    input_serializer: type | None,
    *,
    partial: bool = False,
    extra_data: Mapping[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    instance: Any = None,
) -> Any:
    """Validate ``request.data`` against ``input_serializer``; ``None`` if absent.

    Thin wrapper over :func:`build_input_serializer` (see there for the
    parameter semantics) returning only ``validated_data`` — kept for
    callers that don't need the bound serializer itself.
    """
    serializer = build_input_serializer(
        request,
        input_serializer,
        partial=partial,
        extra_data=extra_data,
        context=context,
        instance=instance,
    )
    return None if serializer is None else serializer.validated_data


def dispatch_service(
    fn: Callable[..., Any],
    kwargs: dict[str, Any],
    *,
    atomic: bool,
) -> Any:
    """Run a service from a sync view, transparently bridging async ones."""
    if is_async(fn):
        return async_to_sync(arun_service)(fn, kwargs, atomic=atomic)
    return run_service(fn, kwargs, atomic=atomic)


def _execute_mutation(
    view: Any,
    request: Request,
    *,
    service: Callable[..., Any],
    input_serializer: type | None,
    output_selector_spec: SelectorSpec[Any, Any] | None,
    atomic: bool,
    success_status: int | Callable[..., int] | None,
    success_default: int,
    render_instance_on_none: bool,
    instance: Any,
    extra_kwargs: dict[str, Any] | None = None,
    extra_input_data: Mapping[str, Any] | None = None,
    input_context: dict[str, Any] | None = None,
    resolve_output_context: Callable[[Any], dict[str, Any]] | None = None,
    response_finalizer: Callable[..., Response | None] | None = None,
    partial: bool = False,
) -> Response:
    """Internal flow runner shared by ``MutationFlowMixin`` and ``@service_action``.

    Steps:
      1. Validate input → bound serializer + ``validated_data``. On
         update / destroy flows the resolved ``instance`` is threaded into
         the serializer (``serializer(instance, data=..., partial=...)``)
         so instance-dependent validation works.
      2. Build kwarg pool (request, user, instance?, data?, serializer?,
         extras).
      3. Resolve service signature against pool, dispatch.
      4. Map ``ServiceError`` → DRF exception on raise.
      5. If ``output_selector_spec`` carries a selector, invoke it (with the
         service result added to the pool as ``result``) and apply the
         spec's queryset shaping; materialize a QuerySet via ``.first()``
         since the nested spec is always retrieve-shaped. Otherwise fall
         back to the in-memory ``instance`` when the service returned None.
      6. Resolve ``success_status`` (an ``int`` verbatim, a callable through
         the status pool ``{result, instance, request, view}``, else
         ``success_default``). A callable keys on the *service's* return value.
      7. Render via the nested spec's ``output_serializer`` (or raw, or an
         empty-body response). A ``None`` result with no output serializer
         renders an empty body at the resolved status when
         ``success_status`` was set, else 204; a selector that returned
         ``None`` always renders 204.
      8. Apply ``response_finalizer`` (2xx only, pre-render) to the built
         ``Response`` — cookies / headers / a swapped response. See
         :func:`~rest_framework_services.views.mutation.apply_response_finalizer.apply_response_finalizer`.

    ``render_instance_on_none`` is the update-vs-destroy intent flag: update
    callers pass ``True`` so a service that mutates in place and returns
    ``None`` still renders the instance; destroy passes ``False`` so a stale
    post-delete instance is never surfaced.

    ``resolve_output_context`` builds the output serializer's ``context=``
    dict. It is called with the *final* ``result`` (post-selector,
    post-fallback) so the output context provider can see — and run a single
    batched query against — the exact instance being serialized. Resolving
    it lazily here, rather than eagerly in :func:`dispatch_mutation_for_spec`,
    is what makes ``result`` available to the provider.

    ``view`` is intentionally absent from the *service/selector* pool: they are
    plain business logic and should not reach back into the calling view. When
    a callable needs view state (URL kwargs, action name, etc.), pipe it
    through ``ServiceSpec.kwargs`` / ``SelectorSpec.kwargs`` instead. The
    ``response_finalizer`` pool is the one documented exception — it *does*
    carry ``view`` (a response decision legitimately needs view/request state).
    """
    serializer_instance: Serializer | None = build_input_serializer(
        request,
        input_serializer,
        partial=partial,
        extra_data=extra_input_data,
        context=input_context,
        instance=instance,
    )
    data: Any = serializer_instance.validated_data if serializer_instance is not None else None
    # Through ``base_pool`` rather than restating the seeds: the set has grown
    # past the two names this used to inline, and a seed present off-HTTP but
    # not here would mean a service that declares it works over one transport
    # and raises a ``TypeError`` over the other.
    pool: dict[str, Any] = base_pool(user=getattr(request, "user", None), request=request)
    if instance is not None:
        pool["instance"] = instance
    if serializer_instance is not None:
        # ``serializer`` is a reserved framework seed (like ``request`` /
        # ``user`` / ``data``): only services that declare it receive the
        # bound, validated serializer — e.g. to call ``.save()`` when
        # persistence lives on the serializer (nested-write patterns).
        pool["data"] = data
        pool["serializer"] = serializer_instance
    if extra_kwargs:
        pool.update(extra_kwargs)

    try:
        result: Any = dispatch_service(
            service,
            resolve_callable_kwargs(service, pool),
            atomic=atomic,
        )
    except ServiceError as exc:
        raise map_service_error(exc) from exc

    # The service's raw return value, captured before an output selector can
    # replace ``result`` below. Both a callable ``success_status`` and the
    # ``response_finalizer`` key on this — the flags carrier (e.g. an upsert
    # DTO's created flag), not a re-fetched output instance.
    service_result: Any = result

    # Mirror DRF's ``UpdateModelMixin``: a mutating service may have changed a
    # related collection the target instance prefetched (via a prefetching
    # ``instance_selector_spec`` or the ``get_object()`` queryset), leaving its
    # ``_prefetched_objects_cache`` stale. Clear it on the resolved in-memory
    # instance so a re-serialization reads fresh related data. Guarded: a no-op
    # on create (``instance`` is ``None``) and when nothing was prefetched. The
    # final ``result`` is deliberately left untouched — an ``output_selector_spec``
    # re-fetch carries its own intentional ``prefetch_related`` that must survive.
    if instance is not None and getattr(instance, "_prefetched_objects_cache", None):
        instance._prefetched_objects_cache = {}

    output_serializer: type[Serializer] | None = None
    if output_selector_spec is not None:
        output_serializer = output_selector_spec.output_serializer
        selector = output_selector_spec.selector
        if selector is not None:
            selector_pool: dict[str, Any] = {**pool, "result": result}
            result = run_selector(
                selector,
                resolve_callable_kwargs(selector, selector_pool),
            )
            result = apply_queryset_shaping(
                result,
                view,
                request,
                select_related=output_selector_spec.select_related,
                prefetch_related=output_selector_spec.prefetch_related,
                annotations=output_selector_spec.annotations,
                extend_queryset=output_selector_spec.extend_queryset,
                # Parity with dispatch_spec's output re-fetch: apply the
                # nested spec's filter_set here too. filter_data falls back to
                # request.query_params (the blessed filter_set source on HTTP),
                # so the same output_selector_spec filters identically on both the
                # HTTP and transport-neutral paths.
                filter_set=output_selector_spec.filter_set,
                source_label="ServiceSpec.output_selector_spec.selector",
            )
            if is_queryset(result):
                # Materialize a QuerySet return to a single instance — the
                # nested spec is retrieve-shaped, so a user can write
                # ``selector=lambda result: Model.objects.filter(pk=result.pk)``
                # and rely on the spec's shaping to apply.
                result = result.first()

    selector_ran: bool = (
        output_selector_spec is not None and output_selector_spec.selector is not None
    )

    if (
        result is None
        and instance is not None
        and render_instance_on_none
        and output_serializer is not None
        and not selector_ran
    ):
        # Update-in-place that returned nothing — render the in-memory instance
        # through the configured output serializer, mirroring DRF's
        # ``UpdateAPIView`` shape. Gated three ways: it needs an output
        # serializer (there is nothing to render a raw model instance with
        # otherwise — see the empty-body branch below); it is keyed on the
        # caller's ``render_instance_on_none`` intent rather than on the status
        # code, so destroy (which passes ``False``) never surfaces a stale
        # post-delete instance even when given a custom success status; and it
        # is skipped when an output selector already ran (its ``None`` return
        # is authoritative).
        result = instance

    # Resolve the success status now that the service has run. A callable
    # ``success_status`` keys on the service's return value (``result``) and the
    # resolved ``instance`` — e.g. an upsert returning 200 vs 201. This status
    # pool is distinct from the service/selector ``pool`` above and, unlike it,
    # deliberately includes ``view``: a status decision may legitimately read
    # view/request context, whereas business logic must not (see the pool note
    # in the docstring).
    status_pool: dict[str, Any] = {"request": request, "view": view, "result": service_result}
    if instance is not None:
        status_pool["instance"] = instance
    resolved_status: int = resolve_success_status(
        success_status, default=success_default, pool=status_pool
    )
    # Empty-body responses fall back to 204 when ``success_status`` is unset;
    # a set int/callable applies uniformly (mirrors the pre-callable behaviour
    # where ``empty_body_status`` was ``spec.success_status`` if set, else 204).
    resolved_empty_status: int = (
        drf_status.HTTP_204_NO_CONTENT if success_status is None else resolved_status
    )

    if output_serializer is not None:
        output_context = resolve_output_context(result) if resolve_output_context else {}
        serializer = output_serializer(result, context=output_context)
        response = Response(serializer.data, status=resolved_status)
    elif result is not None:
        response = Response(result, status=resolved_status)
    elif selector_ran:
        # Empty body. A selector that returned ``None`` is an authoritative
        # no-content result → always 204.
        response = Response(status=drf_status.HTTP_204_NO_CONTENT)
    else:
        # Otherwise honor ``resolved_empty_status`` — the caller's explicitly-set
        # ``success_status`` if any, else 204. This is what lets a destroy (or
        # any no-output mutation) carry a custom success status while a
        # body-less default still reads as 204.
        response = Response(status=resolved_empty_status)

    # 2xx, post-serialization, pre-render: the finalizer may attach cookies /
    # headers or swap the response wholesale. ``result`` is the service's raw
    # return (the flags carrier); ``view`` is deliberately available here.
    return apply_response_finalizer(
        response_finalizer,
        response,
        request=request,
        view=view,
        result=service_result,
        instance=instance,
        data=data,
    )


def _dispatch_bulk_via_spec(
    view: Any,
    request: Request,
    spec: ServiceSpec[Any, Any, Any],
    *,
    default_status: int,
) -> Response:
    """Render a bulk (``many`` / collection) spec through ``dispatch_spec``.

    The list body / collection target, validation, and per-set scoping all live
    in the transport-neutral path; here we only map its
    :class:`~rest_framework_services.DispatchResult` to a DRF ``Response`` and
    translate a ``ServiceError`` the same way the single-instance flow does.

    ``success_status`` resolves through the same rule as the single flow: an
    ``int`` verbatim, a callable through the status pool (``result`` is the
    bulk return value; ``instance`` is absent on a bulk path), else
    ``default_status``. ``spec.response_finalizer`` applies here too (2xx,
    pre-render); the bulk finalizer pool has no ``instance`` / ``data``.
    """
    # Local import: ``dispatch_spec`` composes ``build_input_serializer_from_data``
    # from this module, so the dependency is one-directional only at runtime.
    from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
    from rest_framework_services.dispatch.render_spec_output import render_spec_output

    if spec.many:
        # ``many`` is a list body straight through.
        params: Any = request.data
    else:
        # Collection target: the filter lives in the query string (a DELETE
        # carries no body), merged over any body payload for the service, plus
        # the view's URL kwargs. ``params`` is dispatch_spec's flat mapping —
        # documented as the union of ``request.data`` / ``query_params`` / URL
        # kwargs — so a ``collection_selector_spec`` on a nested route
        # (``/parents/{parent_pk}/children/``) can scope by ``parent_pk``, just
        # as the single-instance path passes ``extra_url_kwargs=view.kwargs`` to
        # its instance selector. Route captures are authoritative: they win over
        # client-supplied query / body on a key conflict, so a filter value
        # can't override the route scope.
        body = request.data if isinstance(request.data, dict) else {}
        url_kwargs = getattr(view, "kwargs", None) or {}
        params = {**request.query_params.dict(), **body, **url_kwargs}

    try:
        result = dispatch_spec(
            spec,
            user=getattr(request, "user", None),
            params=params,
            request=request,
            view=view,
        )
    except ServiceError as exc:
        raise map_service_error(exc) from exc

    status_pool: dict[str, Any] = {"request": request, "view": view, "result": result.value}
    status = resolve_success_status(spec.success_status, default=default_status, pool=status_pool)
    if result.value is None:
        response = Response(status=status)
    else:
        payload = render_spec_output(
            spec,
            result.value,
            many=(result.kind == "list"),
            view=view,
            request=request,
            extras={"result": result.value},
        )
        if status == drf_status.HTTP_204_NO_CONTENT:
            # A bulk op that returns a body but inherited the destroy default.
            status = drf_status.HTTP_200_OK
        response = Response(payload, status=status)

    return apply_response_finalizer(
        spec.response_finalizer,
        response,
        request=request,
        view=view,
        result=result.value,
    )


def dispatch_mutation_for_spec(
    view: Any,
    request: Request,
    spec: ServiceSpec[Any, Any, Any],
    *,
    instance: Any,
    default_status: int,
    render_instance_on_none: bool,
    partial: bool = False,
) -> Response:
    """End-to-end dispatch for one ``ServiceSpec`` call.

    Runs the kwargs-resolution chain (``spec.kwargs`` →
    ``get_<action>_service_kwargs`` → ``get_service_kwargs``) and the
    underlying mutation flow. Used by :class:`MutationFlowMixin`,
    standalone mutation views, and ``@service_action`` so the call shape
    lives in one place.

    ``partial`` is the transport-derived flag (PATCH → ``True``);
    ``spec.partial`` overrides it when set. Being the single call-shape
    point, the override is honoured uniformly across every surface —
    including create dispatch, so a create spec with ``partial=True``
    validates partially.

    A bulk spec (``many=True`` or a ``collection_selector_spec``) is routed
    through the transport-neutral :func:`dispatch_spec` instead of the
    single-instance flow, then rendered for the HTTP response — so the bulk
    rules live in one place.
    """
    if spec.partial is not None:
        partial = spec.partial
    if spec.many or spec.collection_selector_spec is not None:
        return _dispatch_bulk_via_spec(view, request, spec, default_status=default_status)
    action: str | None = getattr(view, "action", None)
    action_kwargs_hook: str | None = f"get_{action}_service_kwargs" if action else None
    action_input_hook: str | None = f"get_{action}_input_data" if action else None
    action_input_context_hook: str | None = (
        f"get_{action}_input_serializer_context" if action else None
    )
    action_output_context_hook: str | None = (
        f"get_{action}_output_serializer_context" if action else None
    )
    extras = resolve_extra_kwargs(
        view,
        request,
        spec_kwargs=spec.kwargs,
        action_hook=action_kwargs_hook,
        catch_all_hook="get_service_kwargs",
    )
    input_extras = resolve_input_extras(
        view,
        request,
        spec_input_data=spec.input_data,
        action_hook=action_input_hook,
        catch_all_hook="get_input_data",
        # Offered to providers that declare ``instance`` (``None`` on
        # create) so pre-validation input mutation can read the current row.
        extras={"instance": instance},
    )
    input_context = resolve_serializer_context(
        view,
        request,
        direction_hook="get_input_serializer_context",
        action_hook=action_input_context_hook,
        spec_provider=spec.input_serializer_context,
    )
    output_spec = spec.output_selector_spec
    output_provider = output_spec.output_serializer_context if output_spec is not None else None

    def resolve_output_context(result: Any) -> dict[str, Any]:
        # Resolved lazily with the final ``result`` so the output context
        # provider can run a single batched query against the exact instance
        # being serialized (offered as the ``result`` extra).
        return resolve_serializer_context(
            view,
            request,
            direction_hook="get_output_serializer_context",
            action_hook=action_output_context_hook,
            spec_provider=output_provider,
            extras={"result": result},
        )

    return _execute_mutation(
        view,
        request,
        service=spec.service,
        input_serializer=spec.input_serializer,
        output_selector_spec=output_spec,
        atomic=spec.atomic,
        success_status=spec.success_status,
        success_default=default_status,
        render_instance_on_none=render_instance_on_none,
        instance=instance,
        extra_kwargs=extras,
        extra_input_data=input_extras,
        input_context=input_context,
        resolve_output_context=resolve_output_context,
        response_finalizer=spec.response_finalizer,
        partial=partial,
    )


def resolve_mutation_instance(
    view: Any,
    spec: ServiceSpec[Any, Any, Any],
) -> Any:
    """Resolve the instance an update / destroy / detail action targets.

    Precedence: ``spec.instance_selector_spec`` (when set with a selector)
    → the view's ``get_object()`` chain (an ``action_specs["retrieve"]``
    selector via :class:`SelectorRetrieveMixin`, else DRF's default
    ``queryset`` / ``lookup_field`` lookup, else a user ``get_object()``
    override). Used by the update / destroy viewset mixins, the standalone
    update / delete views, and ``@service_action`` detail actions so the
    precedence lives in one place.

    The spec path dispatches through :func:`dispatch_selector_for_spec`
    (the standard selector call shape: ``{request, user}`` + the view's
    URL kwargs + the selector extras chain, queryset shaping applied,
    RETRIEVE materialization via ``.first()``). The nested spec's
    ``allow_none`` flag is ignored — a mutation against a missing row is
    always a 404, so a ``None`` resolution raises
    :exc:`~rest_framework.exceptions.NotFound` regardless. Object-level
    permissions run against the resolved instance
    (``view.check_object_permissions``), matching DRF's own ``get_object()``
    contract.

    Returns ``None`` for a **bulk** spec (``many=True`` or a
    ``collection_selector_spec``): there is no single instance, and the
    ``get_object()`` lookup would 404 a body-only bulk endpoint. The bulk path
    resolves its target inside :func:`dispatch_mutation_for_spec` instead.
    """
    if spec.many or spec.collection_selector_spec is not None:
        return None
    instance_spec = spec.instance_selector_spec
    if instance_spec is None or instance_spec.selector is None:
        return view.get_object()
    instance = dispatch_selector_for_spec(
        view,
        instance_spec,
        extra_url_kwargs=getattr(view, "kwargs", None),
        source_label="ServiceSpec.instance_selector_spec.selector",
    )
    if instance is None:
        # Only reachable with ``allow_none=True`` on the nested spec —
        # the flag expresses a nullable *read* contract and is ignored here.
        raise drf_exceptions.NotFound()
    view.check_object_permissions(view.request, instance)
    return instance
