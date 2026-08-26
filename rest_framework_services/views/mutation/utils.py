"""Helpers used by the mutation views, viewset mixins, and ``@service_action``.

The HTTP half either side of ``dispatch_spec`` — view hooks in, response out.
There is deliberately **no** flow runner here: the mutation pipeline lives in
``dispatch_spec``, and a behaviour that belongs to the *spec* belongs in the
core, or it will be honoured on one transport and not the other.

``map_service_error`` is re-imported from its own leaf module rather than
defined here, so ``call_service`` can map errors without importing this one.
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

    The serializer is returned validated (``is_valid(raise_exception=True)`` has
    run) but never saved; the service owns persistence.

    Args:
        request: The request whose ``data`` is validated.
        input_serializer: A bare dataclass type (wrapped in a
            ``DataclassSerializer`` on the fly), a ``DataclassSerializer``
            subclass, or any other ``Serializer`` subclass such as
            ``ModelSerializer``. The first two produce a dataclass instance as
            ``validated_data``, the third a ``dict``.
        partial: Validate partially, as DRF's ``serializer(partial=…)``.
        extra_data: Merged on top of ``request.data`` before the serializer is
            constructed, server-provided keys winning on overlap — the seam the
            ``input_data`` resolver chain uses to lift URL kwargs into serializer
            input. The merge goes through
            ``apply_input_data``,
            which keeps a form-encoded / multipart ``QueryDict``'s scalars from
            flattening into one-element lists.
        context: Forwarded to the serializer's ``context=`` so DRF-style
            ``self.context["request"]`` / ``["view"]`` lookups work inside
            validators and fields.
        instance: The resolved mutation target on update / destroy flows. The
            serializer is constructed DRF-style, so ``self.instance`` is
            populated inside ``validate()`` / field validators and
            instance-aware validators (``UniqueValidator`` excluding the current
            row) behave as under DRF's own update flow.
        many: Validate ``data`` as a list — the bulk list-payload path;
            ``validated_data`` is then a list of items.
    """
    if input_serializer is None:
        return None
    if extra_data:
        data: Any = apply_input_data(request.data, extra_data)
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

    The transport-neutral core of
    [`build_input_serializer`][rest_framework_services.views.mutation.utils.build_input_serializer]:
    it takes the input ``data`` directly instead of reaching into a DRF
    ``request.data``, so a non-HTTP caller (``dispatch_spec``) and the HTTP view path
    share one validation implementation. See
    [`build_input_serializer`][rest_framework_services.views.mutation.utils.build_input_serializer]
    for the remaining parameter semantics."""
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

    Thin wrapper over
    [`build_input_serializer`][rest_framework_services.views.mutation.utils.build_input_serializer]
    (see there for the parameter semantics) returning only ``validated_data``, for
    callers that don't need the bound serializer itself."""
    serializer = build_input_serializer(
        request,
        input_serializer,
        partial=partial,
        extra_data=extra_data,
        context=context,
        instance=instance,
    )
    return None if serializer is None else serializer.validated_data


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
    """Turn a
    [`DispatchResult`][rest_framework_services.types.dispatch_result.DispatchResult]
    into the HTTP ``Response`` for a mutation.

    Everything downstream of the dispatch that is genuinely transport-shaped, and
    nothing that isn't: resolve the success status against the *action's* default
    (201 create / 200 update / 204 destroy), which the core cannot know; fall
    back to the in-memory ``instance`` when an in-place update returned ``None``;
    render through the output serializer or emit a body-less response; apply
    ``spec.response_finalizer`` (2xx, pre-render).

    ``render_instance_on_none`` is the caller's update-vs-destroy intent — read
    it as "the target still exists". Deliberately **not** a spec field and **not**
    transport-neutral: off HTTP the equivalent is ``output_selector_spec``.
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
        # mirroring DRF's ``UpdateAPIView``. Gated on the caller's intent rather
        # than the status code, so destroy never surfaces a stale post-delete row
        # even under a custom success status; and a selector that already ran
        # owns its ``None``.
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

    The list body / collection target, validation, and per-set scoping all live in the
    transport-neutral path; here we only map its
    [`DispatchResult`][rest_framework_services.types.dispatch_result.DispatchResult] to
    a DRF ``Response`` and translate a ``ServiceError`` the same way the single-instance
    flow does. The status and finalizer pools carry no ``instance`` / ``data`` on a bulk
    path."""
    # Local import: ``dispatch_spec`` composes ``build_input_serializer_from_data``
    # from this module, so the dependency is one-directional only at runtime.
    from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
    from rest_framework_services.dispatch.render_spec_output import render_spec_output

    if spec.many:
        # ``many`` is a list body straight through.
        params: Any = request.data
    else:
        # Collection target: a DELETE carries no body, so the filter lives in the
        # query string, merged under any body payload and then under the view's
        # URL kwargs. Route captures go last because they are authoritative — a
        # client-supplied filter value must not override the route scope.
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

    Used by
    [`MutationFlowMixin`][rest_framework_services.views.mutation.mutation_flow_mixin.MutationFlowMixin],
    the standalone mutation views, and ``@service_action`` so the call shape lives in
    one place: resolve the view's hook chains (``resolve_view_hooks``), dispatch through
    [`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec],
    render (``render_mutation_response``). Only the first and last steps are HTTP's. The
    target is passed in rather than resolved in the core, because HTTP's
    ``get_object()`` chain has no off-HTTP meaning; a bulk spec takes the same route
    with its own params assembly and renderer.

    ``partial`` is the transport-derived flag (PATCH → ``True``) and
    ``spec.partial`` overrides it when set. Being the single call-shape point,
    the override is honoured uniformly across every surface — including create
    dispatch, so a create spec with ``partial=True`` validates partially.
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
        # Off HTTP a matchless ``instance_selector_spec`` is a neutral
        # ``not_found`` for the transport to map; here it is DRF's 404. The
        # nested spec's ``allow_none`` stays ignored — it expresses a nullable
        # *read* contract, and a mutation against a missing row is always a 404.
        raise NotFound()

    def output_context(value: Any) -> dict[str, Any]:
        # Resolved lazily with the final value so the output context provider can
        # run a single batched query against the exact instance being serialized.
        # Uses the four-layer resolver rather than the ``ViewHooks`` carrier: the
        # carrier exists to cross the boundary *into* the dispatch core, and
        # rendering never crosses it.
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

    Returns: ``None`` for a **bulk** spec (``many=True`` or a
        ``collection_selector_spec``): there is no single instance, and the
        ``get_object()`` lookup would 404 a body-only bulk endpoint. ``UNSET`` when the
        spec carries an ``instance_selector_spec`` — *the core resolves it*, with the
        right kwarg pool, error label, and reserved-seed strip. Object permissions still
        run: the core fires ``on_target_resolved`` against the resolved target and the
        HTTP caller passes ``check_view_object_permissions``. Otherwise the view's
        ``get_object()`` chain (an ``action_specs["retrieve"]`` selector via
        [`SelectorRetrieveMixin`][rest_framework_services.viewsets.selector_retrieve_mixin.SelectorRetrieveMixin],
        else DRF's ``queryset`` / ``lookup_field`` lookup, else a user override) — the
        one branch that is genuinely HTTP-only and so cannot move.

    **``filter_backends`` do not apply to the two spec-driven branches.** DRF
    runs ``filter_queryset()`` inside its own ``get_object()``, so an
    ``instance_selector_spec`` (resolved by the core) and a retrieve selector
    (which overrides ``get_object()``) both bypass it, exactly as a hand-written
    ``get_object()`` override does. A tenant-scoping backend in
    ``DEFAULT_FILTER_BACKENDS`` therefore does not narrow the row a PATCH or
    DELETE may reach here, while the sibling list action stays scoped. Put the
    scoping in the selector's own queryset. Only the last branch — DRF's own
    ``get_object()`` — applies the backends.
    """
    if spec.many or spec.collection_selector_spec is not None:
        return None
    instance_spec = spec.instance_selector_spec
    if instance_spec is not None and instance_spec.selector is not None:
        return UNSET
    return view.get_object()
