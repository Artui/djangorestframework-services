"""Internal helpers shared by the transport-neutral dispatch + render surface.

Nothing here is exported from the package's public API; the public entry
points are :func:`~rest_framework_services.dispatch_spec`,
:func:`~rest_framework_services.adispatch_spec`, and
:func:`~rest_framework_services.render_spec_output`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from asgiref.sync import sync_to_async

from rest_framework_services.is_async import is_async
from rest_framework_services.selectors.utils import apply_queryset_shaping
from rest_framework_services.services.arun_service import arun_service
from rest_framework_services.services.run_service import run_service
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.utils import resolve_callable_kwargs

# Labels handed to ``apply_queryset_shaping`` so a misconfiguration points at
# the offending spec field.
SELECTOR_SOURCE = "SelectorSpec.selector"
INSTANCE_SOURCE = "ServiceSpec.instance_selector_spec.selector"
OUTPUT_SOURCE = "ServiceSpec.output_selector_spec.selector"


def base_pool(*, user: Any, request: Any) -> dict[str, Any]:
    """The two seeds every dispatched callable's pool carries."""
    return {"request": request, "user": user}


def resolve_provider(provider: Callable[..., Any] | None, pool: dict[str, Any]) -> dict[str, Any]:
    """Invoke a ``spec.kwargs`` / context provider through the keyword pool.

    Mirrors the PROV-1 invocation convention: the provider receives only the
    subset of ``pool`` it declares. Returns ``{}`` when ``provider`` is ``None``.
    """
    if provider is None:
        return {}
    return dict(provider(**resolve_callable_kwargs(provider, pool)))


def shape_queryset(
    spec: SelectorSpec[Any, Any],
    qs: Any,
    *,
    view: Any,
    request: Any,
    params: Mapping[str, Any],
    source_label: str,
) -> Any:
    """Apply a selector spec's queryset shaping, ``params`` as the filter data."""
    return apply_queryset_shaping(
        qs,
        view,
        request,
        select_related=spec.select_related,
        prefetch_related=spec.prefetch_related,
        annotations=spec.annotations,
        extend_queryset=spec.extend_queryset,
        filter_set=spec.filter_set,
        filter_data=params,
        source_label=source_label,
    )


def output_serializer_for(spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]) -> type | None:
    """The output serializer class for either spec kind (``None`` when unset)."""
    if isinstance(spec, SelectorSpec):
        return spec.output_serializer
    out = spec.output_selector_spec
    return out.output_serializer if out is not None else None


def _output_context_provider(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
) -> Callable[..., Any] | None:
    if isinstance(spec, SelectorSpec):
        return spec.output_serializer_context
    out = spec.output_selector_spec
    return out.output_serializer_context if out is not None else None


def resolve_output_context(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    *,
    view: Any,
    request: Any,
    extras: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Resolve the output serializer context for either spec kind, or ``None``.

    ``extras`` carries the resolved-data keyword the provider may declare
    (``result`` for a mutation, ``instance`` for a retrieve, ``page`` for a
    list). Invoked through the PROV-1 keyword pool.
    """
    provider = _output_context_provider(spec)
    if provider is None:
        return None
    pool: dict[str, Any] = {"view": view, "request": request, **extras}
    return dict(provider(**resolve_callable_kwargs(provider, pool)))


def resolve_input_context(
    spec: ServiceSpec[Any, Any, Any],
    *,
    view: Any,
    request: Any,
) -> dict[str, Any] | None:
    """Resolve ``ServiceSpec.input_serializer_context``, or ``None``."""
    return (
        resolve_provider(spec.input_serializer_context, {"view": view, "request": request}) or None
    )


async def arun_callable(
    fn: Callable[..., Any] | Callable[..., Awaitable[Any]],
    kwargs: dict[str, Any],
) -> Any:
    """Run a selector / instance-resolver from async code, DB-safe either way.

    Async callables are awaited; sync callables run in Django's thread-sensitive
    executor so their ORM access doesn't trip ``SynchronousOnlyOperation``.
    """
    if is_async(fn):
        return await fn(**kwargs)
    return await sync_to_async(fn, thread_sensitive=True)(**kwargs)


async def arun_service_callable(
    fn: Callable[..., Any] | Callable[..., Awaitable[Any]],
    kwargs: dict[str, Any],
    *,
    atomic: bool,
) -> Any:
    """Run a service from async code (optionally atomic), DB-safe either way."""
    if is_async(fn):
        return await arun_service(fn, kwargs, atomic=atomic)
    return await sync_to_async(run_service, thread_sensitive=True)(fn, kwargs, atomic=atomic)
