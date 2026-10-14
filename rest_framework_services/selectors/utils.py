"""Internal selector dispatch helpers (sync + async)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from django.db.models import Model
from django.http import QueryDict
from django_service_specs.adapters.filters.filter_set_parameters import filter_set_parameters
from django_service_specs.parameters.coerce_flat import coerce_flat
from django_service_specs.parameters.parameters import Parameters

# The kernel's own. Re-exported under ``as`` because this module is the path drfs
# has always exported them from, and ``dispatch/`` and the views still import them
# from here.
from django_service_specs.selectors.amaterialize_retrieve import (
    amaterialize_retrieve as amaterialize_retrieve,
)
from django_service_specs.selectors.arun_selector import arun_selector as arun_selector
from django_service_specs.selectors.is_queryset import is_queryset as is_queryset
from django_service_specs.selectors.materialize_retrieve import (
    materialize_retrieve as materialize_retrieve,
)
from django_service_specs.selectors.run_selector import run_selector as run_selector
from django_service_specs.selectors.shape_queryset import shape_queryset
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from rest_framework.exceptions import NotFound
from rest_framework.request import Request

from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.views.utils import resolve_view_hooks


def apply_queryset_shaping(
    qs: Any,
    view: Any,
    request: Request,
    *,
    select_related: Any,
    prefetch_related: Any,
    annotations: Any,
    extend_queryset: Any,
    filter_set: Any = None,
    filter_data: Any = None,
    source_label: str,
) -> Any:
    """Apply the five shaping fields to ``qs``, through the kernel's ``shape_queryset``.

    drfs' signature over django-service-specs' shaping, for a caller holding a
    view and a request rather than a spec: the fields become a kernel
    ``SelectorSpec`` (with a stand-in selector it never runs), and ``view``,
    ``request`` and the filter's arguments its pool. The order is the kernel's:
    the declarative fields first (in declaration order), then
    ``extend_queryset``, so the user callable always sees the fully
    statically-shaped queryset, and finally ``filter_set``, so filtering
    composes with shaping and runs before the retrieve ``.first()``
    materialization the caller does next.

    Args:
        extend_queryset: Called as ``extend_queryset(qs, view, request)``, by
            position, as it always was; the kernel binds its own by name.
        filter_set: A django-filter ``FilterSet``, validated before ``.qs`` is
            read, and handed ``request`` through its constructor, so a
            request-scoped one sees the ``self.request`` it would behind
            ``DjangoFilterBackend``.
        filter_data: The mapping the filter's arguments are read from, each of
            its filters' names and nothing else. ``None`` falls back to
            ``request.query_params``, the HTTP view path. A ``QueryDict`` is
            read flat as dispatch reads a query string: a repeated key as a list
            where the filter takes several values, and each value coerced to the
            type the filter declares.
        source_label: Named in the misconfiguration error to point at the
            offending callable (``"SelectorSpec.selector"`` vs
            ``"ServiceSpec.output_selector_spec.selector"``).

    Returns:
        The shaped queryset, or ``qs`` unchanged when nothing is configured.

    Raises:
        ImproperlyConfigured: Shaping is configured but ``qs`` is not a Django
            queryset; ``filter_set`` is not a django-filter ``FilterSet`` (one
            duck-typed on ``(data, queryset) -> .qs`` is refused); or
            ``extend_queryset`` answered with an awaitable.
        InvalidArguments: The kernel's, where ``filter_set`` refused its data or
            a value no integer column can hold, keyed as the ``FilterSet`` keys
            its errors. It reaches the caller as the kernel raised it, not as
            DRF's ``ValidationError``: nothing here translates it, so a caller
            inside a DRF view that wants a 400 translates it itself.
    """
    spec = KernelSelectorSpec(
        kind=SelectorKind.LIST,
        selector=_never_run,
        select_related=select_related,
        prefetch_related=prefetch_related,
        annotations=annotations,
        extend_queryset=_positional(extend_queryset) if extend_queryset is not None else None,
        filter_set=filter_set,
    )
    pool: dict[str, Any] = {"view": view, "request": request}
    if filter_set is not None:
        source = filter_data if filter_data is not None else request.query_params
        pool.update(_filter_arguments(filter_set_parameters(filter_set), source))
    return shape_queryset(qs, spec, pool, source_label=source_label)


def _never_run() -> None:
    """The selector the kernel's spec requires; shaping never calls it."""


def _positional(extend: Callable[..., Any]) -> Callable[..., Any]:
    """drfs' positional ``extend_queryset`` as a callable the kernel binds by name.

    No ``functools.wraps``: it sets ``__wrapped__``, which ``inspect.signature``
    follows, and the kernel would read the wrapped callable's own names.
    """

    def extend_queryset(queryset: Any, view: Any, request: Any) -> Any:
        return extend(queryset, view, request)

    return extend_queryset


def _filter_arguments(parameters: Parameters, source: Mapping[str, Any]) -> dict[str, Any]:
    """The filter's own names in ``source``; a ``QueryDict`` read flat and coerced.

    A ``QueryDict`` holds a list per key, so each name takes every value where
    its Parameter is an array and the last one otherwise, as ``QueryDict``
    reads a repeated key, then ``coerce_flat`` reads the strings as their
    declared types. Held by
    ``test_a_repeated_query_key_reaches_a_multi_valued_filter_whole`` and
    ``test_filter_data_replaces_the_query_string`` (a plain mapping).
    """
    present = [param for param in parameters if param.name in source]
    if not isinstance(source, QueryDict):
        return {param.name: source[param.name] for param in present}
    flat = {
        param.name: source.getlist(param.name) if param.type == "array" else source.get(param.name)
        for param in present
    }
    return coerce_flat(parameters, flat)


def check_view_object_permissions(
    spec: Any,
    context: Any,
    *,
    instance: Any = None,
) -> None:
    """[`TargetGuard`][rest_framework_services.types.target_guard.TargetGuard] running
    DRF's object-permission check for an HTTP view.

    The HTTP counterpart of
    [`enforce_permissions`][rest_framework_services.dispatch.enforce_permissions.enforce_permissions]:
    off HTTP a transport enforces ``spec.permission_classes`` itself, while a DRF view
    has already instantiated them and exposes ``check_object_permissions``. Both plug
    into the same ``on_target_resolved`` seam, so the core stays authz-agnostic on every
    transport.

    Gated on ``Model``, like ``enforce_permissions``: the core fires this hook on
    the LIST branch too, with the resolved **queryset**, and object permissions
    are per-row. ``None`` — a create, or a bulk list payload — is skipped too.
    """
    if isinstance(instance, Model):
        context.view.check_object_permissions(context.request, instance)


def dispatch_selector_for_spec(view: Any, spec: SelectorSpec[Any, Any]) -> Any:
    """End-to-end dispatch for one ``SelectorSpec`` call from a DRF view.

    Resolves the view's ``get_selector_kwargs`` / ``get_<action>_selector_kwargs`` chain
    into a [`ViewHooks`][rest_framework_services.types.view_hooks.ViewHooks] carrier,
    hands off to the one
    [`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec]
    core, and translates the neutral
    [`DispatchResult`][rest_framework_services.types.dispatch_result.DispatchResult]
    into the view-layer contract: a RETRIEVE that resolved nothing raises ``NotFound``,
    unless the spec sets ``allow_none=True`` (then ``None``, and the retrieve views
    render 200 + JSON ``null``). ``SelectorKind.LIST`` returns the shaped queryset.

    **``argument_binding=BUNDLE`` is what keeps HTTP semantics.** Off HTTP the
    flat ``params`` mapping *is* the argument channel; over HTTP a selector's
    kwargs come from route captures plus the hook chain, and the query string
    belongs to ``filter_set`` / the filter backends. ``BUNDLE`` spreads nothing,
    so passing ``query_params`` feeds the filter without widening the argument
    channel.

    There is deliberately no ``extra_url_kwargs`` parameter: the core reads
    ``view.kwargs`` itself via ``view_url_kwargs``, which **strips the reserved
    pool seeds**, so a nested route like ``/users/<user>/posts/`` cannot let the
    captured value shadow the authenticated ``user``. Taking the mapping from a
    caller would reopen that.

    The caller must check ``spec.selector is not None`` first and fall back to
    vanilla DRF otherwise.
    """
    # Local import: ``dispatch_spec`` composes ``run_selector`` /
    # ``materialize_retrieve`` from this module, so the cycle is real and the
    # dependency one-directional only at runtime.
    from rest_framework_services.dispatch.dispatch_spec import dispatch_spec

    assert spec.selector is not None  # noqa: S101 — caller guarantees this
    request = view.request
    result = dispatch_spec(
        spec,
        user=getattr(request, "user", None),
        params={},
        filter_data=request.query_params,
        request=request,
        view=view,
        argument_binding=ArgumentBinding.BUNDLE,
        on_target_resolved=check_view_object_permissions,
        view_hooks=resolve_view_hooks(view, request, chain="selector"),
    )
    if result.kind == "not_found":
        raise NotFound()
    return result.value
