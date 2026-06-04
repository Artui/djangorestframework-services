"""Internal selector dispatch helpers (sync + async)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.db.models import QuerySet
from django.db.models.manager import BaseManager
from rest_framework.exceptions import NotFound
from rest_framework.request import Request

from rest_framework_services._compat.is_async import is_async
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.views.utils import (
    resolve_callable_kwargs,
    resolve_extra_kwargs,
)


def is_queryset(obj: Any) -> bool:
    """True for Django ``QuerySet`` objects and ``Manager`` instances.

    These are the queryset-shaping targets: the things the four shaping
    fields can be applied to, and the things a RETRIEVE selector / output
    selector should be materialized from via ``.first()``. Centralizes the
    "is this a queryset?" decision so the selector and mutation dispatch
    paths agree on one definition instead of duck-typing on a method name
    (``hasattr(..., "first")``), which would also match an unrelated domain
    object that happens to expose ``first``. ``QuerySet`` subclasses
    (``.values()``, ``.values_list()``, polymorphic querysets, …) all pass.
    """
    return isinstance(obj, (QuerySet, BaseManager))


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
    (``"SelectorSpec.selector"`` vs ``"ServiceSpec.output_selector_spec.selector"``).
    """
    if (
        select_related is None
        and prefetch_related is None
        and annotations is None
        and extend_queryset is None
    ):
        return qs
    if not is_queryset(qs):
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
    source_label: str = "SelectorSpec.selector",
) -> Any:
    """End-to-end dispatch for one ``SelectorSpec`` call.

    Runs the kwargs-resolution chain (``spec.kwargs`` →
    ``get_<action>_selector_kwargs`` → ``get_selector_kwargs``), filters
    the resulting pool against the selector's signature, invokes the
    selector sync-or-async, then applies declarative + dynamic queryset
    shaping. Used by both selector viewset mixins and the standalone
    selector views so the call shape lives in one place.

    When ``spec.kind`` is :attr:`SelectorKind.RETRIEVE`, the returned
    QuerySet (if any) is materialized via ``.first()`` and ``None`` /
    :exc:`~django.core.exceptions.ObjectDoesNotExist` are translated to
    :exc:`~rest_framework.exceptions.NotFound`. ``SelectorKind.LIST``
    returns the (optionally shaped) selector return as-is.

    The caller must check ``spec.selector is not None`` before calling and
    fall back to vanilla DRF otherwise.

    ``source_label`` is forwarded to :func:`apply_queryset_shaping` for
    error messages, so a misconfiguration on a nested
    ``output_selector_spec`` points at the right place.
    """
    selector = spec.selector
    assert selector is not None  # noqa: S101 — caller guarantees this
    request = view.request
    action: str | None = getattr(view, "action", None)
    action_hook: str | None = f"get_{action}_selector_kwargs" if action else None

    try:
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
        result = apply_queryset_shaping(
            result,
            view,
            request,
            select_related=spec.select_related,
            prefetch_related=spec.prefetch_related,
            annotations=spec.annotations,
            extend_queryset=spec.extend_queryset,
            source_label=source_label,
        )
    except ObjectDoesNotExist as exc:
        if spec.kind is SelectorKind.RETRIEVE:
            raise NotFound() from exc
        raise

    if spec.kind is not SelectorKind.RETRIEVE:
        return result
    instance = result.first() if is_queryset(result) else result
    if instance is None:
        raise NotFound()
    return instance
