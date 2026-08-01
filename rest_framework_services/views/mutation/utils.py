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

- ``resolve_view_hooks`` — collect the view's hook chains into a ``ViewHooks``
  carrier for the dispatch core (view layers only; the spec's own providers
  stay the core's job).
- ``render_mutation_response`` — turn a ``DispatchResult`` into the HTTP
  ``Response``: status against the action default, output serializer, finalizer.

There is deliberately **no** flow runner here any more. The mutation pipeline
lives in ``dispatch_spec``, and this module is the HTTP half either side of it —
hooks in, response out. A behaviour that belongs to the *spec* belongs in the
core, or it will be honoured on one transport and not the other.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import is_dataclass, replace
from typing import Any

from rest_framework import status as drf_status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services.dispatch.apply_input_data import apply_input_data
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.selectors.utils import (
    check_view_object_permissions,
)
from rest_framework_services.services.run_service import run_service
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.unset import UNSET
from rest_framework_services.views.mutation.apply_response_finalizer import (
    apply_response_finalizer,
)
from rest_framework_services.views.mutation.map_service_error import (
    map_service_error,
)
from rest_framework_services.views.mutation.resolve_success_status import resolve_success_status
from rest_framework_services.views.utils import (
    resolve_serializer_context,
    resolve_view_hooks,
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

    Delegates to :func:`~rest_framework_services.dispatch.utils.apply_input_data`,
    which owns the merge (including the QueryDict handling a form-encoded body
    needs) for every transport. Kept as a name here because
    :func:`build_input_serializer` is public and reads better for it.
    """
    return apply_input_data(request_data, extra_data)


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
    """Run a service from a sync view, transparently bridging async ones.

    Retained as the view layer's name for the call; the async bridge itself now
    lives in :func:`~rest_framework_services.services.run_service.run_service`, so
    the HTTP and transport-neutral paths cannot disagree about whether an
    ``async def`` service gets awaited. This wrapper is a pure alias — do not
    reintroduce logic here.
    """
    return run_service(fn, kwargs, atomic=atomic)


def render_mutation_response(
    view: Any,
    request: Request,
    spec: ServiceSpec[Any, Any, Any],
    result: DispatchResult,
    *,
    instance: Any,
    default_status: int,
    render_instance_on_none: bool,
    output_context: Callable[[Any], dict[str, Any]],
) -> Response:
    """Turn a :class:`DispatchResult` into the HTTP ``Response`` for a mutation.

    Everything downstream of the dispatch that is genuinely transport-shaped, and
    nothing that isn't. The pipeline itself — validate → pool → service → output
    selector → status — lives in ``dispatch_spec``; this is the half that only
    means something over HTTP:

    1. Resolve the success status against the *action's* default (201 create /
       200 update / 204 destroy), which the core cannot know. A callable
       ``spec.success_status`` keys on ``result.service_result`` — the service's
       own return, the flags carrier — not on the post-selector value.
    2. Fall back to the in-memory ``instance`` when an in-place update returned
       ``None``. See ``render_instance_on_none`` below.
    3. Render through the output serializer, or emit a body-less response.
    4. Apply ``spec.response_finalizer`` (2xx, pre-render).

    ``render_instance_on_none`` is the caller's update-vs-destroy intent. It is
    deliberately **not** a spec field and deliberately **not** transport-neutral:
    off HTTP the equivalent is ``output_selector_spec``, and the flag's only
    load-bearing use is destroy — ``@service_action`` passes ``detail``, but a
    non-detail action has no instance, so the ``instance is not None`` gate below
    already decides those. What it really means is "the target still exists".
    """
    value: Any = result.value
    selector_ran: bool = (
        spec.output_selector_spec is not None and spec.output_selector_spec.selector is not None
    )
    output_serializer: type[Serializer] | None = (
        spec.output_selector_spec.output_serializer
        if spec.output_selector_spec is not None
        else None
    )

    if (
        value is None
        and instance is not None
        and render_instance_on_none
        and output_serializer is not None
        and not selector_ran
    ):
        # Update-in-place that returned nothing — render the in-memory instance,
        # mirroring DRF's ``UpdateAPIView``. Gated three ways: it needs an output
        # serializer (nothing else could render a raw model instance); it keys on
        # the caller's intent rather than the status code, so destroy never
        # surfaces a stale post-delete row even with a custom success status; and
        # a selector that already ran owns its ``None``.
        value = instance

    status_pool: dict[str, Any] = {
        "request": request,
        "view": view,
        "result": result.service_result,
    }
    if instance is not None:
        status_pool["instance"] = instance
    resolved_status: int = resolve_success_status(
        spec.success_status, default=default_status, pool=status_pool
    )
    # Empty-body responses fall back to 204 when ``success_status`` is unset; a
    # set int/callable applies uniformly.
    resolved_empty_status: int = (
        drf_status.HTTP_204_NO_CONTENT if spec.success_status is None else resolved_status
    )

    if output_serializer is not None:
        response = Response(
            output_serializer(value, context=output_context(value)).data, status=resolved_status
        )
    elif value is not None:
        response = Response(value, status=resolved_status)
    elif selector_ran:
        # A selector that returned ``None`` is an authoritative no-content result.
        response = Response(status=drf_status.HTTP_204_NO_CONTENT)
    else:
        response = Response(status=resolved_empty_status)

    return apply_response_finalizer(
        spec.response_finalizer,
        response,
        request=request,
        view=view,
        result=result.service_result,
        instance=instance,
        data=result.data,
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

    view_hooks = resolve_view_hooks(view, request)
    try:
        result = dispatch_spec(
            spec,
            user=getattr(request, "user", None),
            params=params,
            request=request,
            view=view,
            view_hooks=view_hooks,
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
            view_hooks=view_hooks,
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
    """End-to-end dispatch for one ``ServiceSpec`` call over HTTP.

    Used by :class:`MutationFlowMixin`, the standalone mutation views, and
    ``@service_action`` so the call shape lives in one place.

    Three steps, and the middle one is not HTTP's:

    1. **Resolve the view's hook chains** (:func:`resolve_view_hooks`) — the
       ``get_service_kwargs`` / ``get_input_data`` / serializer-context methods
       and their per-action twins. These are methods on a DRF view, so the core
       cannot reach them; the view resolves them and hands them down.
    2. **Dispatch** through :func:`~rest_framework_services.dispatch_spec` — the
       single pipeline, shared with MCP and every other transport. The target is
       passed in rather than resolved there, because HTTP's ``get_object()``
       chain (view ``queryset`` / ``lookup_field`` / a user override) has no
       off-HTTP meaning.
    3. **Render** (:func:`render_mutation_response`) — status against the
       action's default, the output serializer, the finalizer.

    ``partial`` is the transport-derived flag (PATCH → ``True``);
    ``spec.partial`` overrides it when set. Being the single call-shape point,
    the override is honoured uniformly across every surface — including create
    dispatch, so a create spec with ``partial=True`` validates partially.

    A bulk spec (``many=True`` or a ``collection_selector_spec``) takes the same
    route with its own params assembly and renderer.
    """
    # Local import: ``dispatch_spec`` composes ``build_input_serializer_from_data``
    # from this module, so the dependency is one-directional only at runtime.
    from rest_framework_services.dispatch.dispatch_spec import dispatch_spec

    if spec.partial is not None:
        partial = spec.partial
    if spec.many or spec.collection_selector_spec is not None:
        return _dispatch_bulk_via_spec(view, request, spec, default_status=default_status)

    # ``instance`` may be UNSET ("the core resolves it"); the view-side
    # providers and the renderer only ever see a real target or ``None``.
    resolved: Any = None if instance is UNSET else instance
    view_hooks = resolve_view_hooks(view, request, instance=resolved)
    # ``spec.partial`` is applied above and re-read by the core, so pass the
    # resolved flag through the spec-shaped seam the core already honours.
    resolved_spec = spec if spec.partial is not None else replace(spec, partial=partial)

    try:
        result = dispatch_spec(
            resolved_spec,
            user=getattr(request, "user", None),
            params=request.data,
            filter_data=request.query_params,
            request=request,
            view=view,
            instance=instance,
            view_hooks=view_hooks,
            on_target_resolved=check_view_object_permissions,
        )
    except ServiceError as exc:
        raise map_service_error(exc) from exc

    if result.kind == "not_found":
        # The core resolved ``instance_selector_spec`` and matched nothing. Off
        # HTTP that is a neutral ``not_found`` for the transport to map; here it
        # is DRF's 404. The nested spec's ``allow_none`` stays ignored — it
        # expresses a nullable *read* contract, and a mutation against a missing
        # row is always a 404.
        raise NotFound()

    def output_context(value: Any) -> dict[str, Any]:
        # Resolved lazily with the final value so the output context provider can
        # run a single batched query against the exact instance being serialized.
        # Uses the four-layer resolver directly rather than the ``ViewHooks``
        # carrier: the carrier exists to cross the boundary *into* the dispatch
        # core, and rendering never crosses it — it stays here.
        return resolve_serializer_context(
            view,
            request,
            direction_hook="get_output_serializer_context",
            action_hook=f"get_{getattr(view, 'action', None)}_output_serializer_context"
            if getattr(view, "action", None)
            else None,
            spec_provider=(
                spec.output_selector_spec.output_serializer_context
                if spec.output_selector_spec is not None
                else None
            ),
            extras={"result": value},
        )

    return render_mutation_response(
        view,
        request,
        spec,
        result,
        instance=result.instance,
        default_status=default_status,
        render_instance_on_none=render_instance_on_none,
        output_context=output_context,
    )


def resolve_mutation_instance(
    view: Any,
    spec: ServiceSpec[Any, Any, Any],
) -> Any:
    """Resolve the mutation target, or defer to the core.

    Three outcomes:

    - ``None`` for a **bulk** spec (``many=True`` or a ``collection_selector_spec``):
      there is no single instance, and the ``get_object()`` lookup would 404 a
      body-only bulk endpoint.
    - :data:`UNSET` when the spec carries an ``instance_selector_spec`` — *the
      core resolves it*. ``dispatch_spec`` already does this for every other
      transport, with the right kwarg pool, the right error label
      (``ServiceSpec.instance_selector_spec.selector``), and the reserved-seed
      strip; resolving it a second time here only created a path that could
      drift from it. Object permissions still run — the core fires
      ``on_target_resolved`` against the resolved target, and the HTTP caller
      passes :func:`check_view_object_permissions`.
    - the view's ``get_object()`` chain otherwise (an ``action_specs["retrieve"]``
      selector via :class:`SelectorRetrieveMixin`, else DRF's default ``queryset``
      / ``lookup_field`` lookup, else a user ``get_object()`` override). This is
      the one branch that is genuinely HTTP-only and so cannot move.
    """
    if spec.many or spec.collection_selector_spec is not None:
        return None
    instance_spec = spec.instance_selector_spec
    if instance_spec is not None and instance_spec.selector is not None:
        return UNSET
    return view.get_object()
