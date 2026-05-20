"""Internal selector dispatch helpers (sync + async)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from rest_framework.exceptions import NotFound
from rest_framework.request import Request

from rest_framework_services._compat.is_async import is_async
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.views.utils import (
    resolve_callable_kwargs,
    resolve_extra_kwargs,
)


def run_selector(fn: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    """Call a selector from sync code, transparently bridging async ones."""
    if is_async(fn):
        return async_to_sync(fn)(**kwargs)
    return fn(**kwargs)


async def arun_selector(
    fn: Callable[..., Any] | Callable[..., Awaitable[Any]],
    kwargs: dict[str, Any],
) -> Any:
    """Call a selector from async code; sync ones run inline."""
    if is_async(fn):
        return await fn(**kwargs)
    return fn(**kwargs)


def apply_queryset_shaping(
    qs: Any,
    view: Any,
    request: Request,
    *,
    select_related: Any,
    prefetch_related: Any,
    annotations: Any,
    extend_queryset: Any,
    source_label: str,
) -> Any:
    """Apply the four shaping fields to ``qs``.

    Declarative fields apply first (in declaration order), then
    ``extend_queryset`` runs so the user callable always sees the fully
    statically-shaped queryset. Returns ``qs`` unchanged when no shaping
    is configured.

    Raises :exc:`ImproperlyConfigured` when shaping is configured but
    ``qs`` is not a Django QuerySet (no ``annotate`` method) — loud
    failure beats a stale ``AttributeError`` deep in DRF rendering.
    ``source_label`` is included in the error to point at the misuse
    (``"SelectorSpec.selector"`` vs ``"ServiceSpec.output_selector"``).

    Shared by :func:`dispatch_selector_for_spec` (selector-backed reads)
    and :func:`_execute_mutation` (the ``output_selector`` step of a
    mutation flow); both specs carry the same field names with identical
    semantics.
    """
    if (
        select_related is None
        and prefetch_related is None
        and annotations is None
        and extend_queryset is None
    ):
        return qs
    if not hasattr(qs, "annotate"):
        raise ImproperlyConfigured(
            "select_related / prefetch_related / annotations / extend_queryset "
            f"are set on the spec but {source_label} returned "
            f"{type(qs).__name__}, which is not a Django QuerySet. Drop the "
            "shaping fields or have the callable return a QuerySet."
        )
    if select_related is not None:
        qs = qs.select_related(*select_related)
    if prefetch_related is not None:
        qs = qs.prefetch_related(*prefetch_related)
    if annotations is not None:
        qs = qs.annotate(**annotations)
    if extend_queryset is not None:
        qs = extend_queryset(qs, view, request)
    return qs


def dispatch_selector_for_spec(
    view: Any,
    spec: SelectorSpec[Any, Any],
    *,
    extra_url_kwargs: dict[str, Any] | None = None,
) -> Any:
    """End-to-end dispatch for one ``SelectorSpec`` call.

    Runs the kwargs-resolution chain (``spec.kwargs`` →
    ``get_<action>_selector_kwargs`` → ``get_selector_kwargs``), filters
    the resulting pool against the selector's signature, invokes the
    selector sync-or-async, then applies declarative + dynamic queryset
    shaping. Used by both selector viewset mixins and the standalone
    selector views so the call shape lives in one place.

    The caller must check ``spec.selector is not None`` before calling and
    fall back to vanilla DRF otherwise.
    """
    selector = spec.selector
    assert selector is not None  # noqa: S101 — caller guarantees this
    request = view.request
    action: str | None = getattr(view, "action", None)
    action_hook: str | None = f"get_{action}_selector_kwargs" if action else None
    extras = resolve_extra_kwargs(
        view,
        request,
        spec_kwargs=spec.kwargs,
        action_hook=action_hook,
        catch_all_hook="get_selector_kwargs",
    )
    pool: dict[str, Any] = {
        "request": request,
        "user": getattr(request, "user", None),
        **(extra_url_kwargs or {}),
        **extras,
    }
    result = run_selector(selector, resolve_callable_kwargs(selector, pool))
    return apply_queryset_shaping(
        result,
        view,
        request,
        select_related=spec.select_related,
        prefetch_related=spec.prefetch_related,
        annotations=spec.annotations,
        extend_queryset=spec.extend_queryset,
        source_label="SelectorSpec.selector",
    )


def dispatch_retrieve_selector(
    view: Any,
    spec: SelectorSpec[Any, Any],
    *,
    extra_url_kwargs: dict[str, Any] | None = None,
) -> Any:
    """Like :func:`dispatch_selector_for_spec`, with retrieve-flavoured 404s.

    Wraps :exc:`~django.core.exceptions.ObjectDoesNotExist` and a ``None``
    return as :exc:`~rest_framework.exceptions.NotFound`. Used by both the
    standalone retrieve view and the retrieve viewset mixin.

    If the selector returns a QuerySet (which is the natural shape when
    spec-level shaping is configured — declarative fields and
    ``extend_queryset`` only apply to QuerySets), the dispatcher
    materializes it via ``.first()`` after shaping has run. Selectors that
    return a single instance directly keep working unchanged.
    """
    try:
        result = dispatch_selector_for_spec(view, spec, extra_url_kwargs=extra_url_kwargs)
    except ObjectDoesNotExist as exc:
        raise NotFound() from exc
    instance = result.first() if hasattr(result, "first") else result
    if instance is None:
        raise NotFound()
    return instance
