"""Internal selector dispatch helpers (sync + async)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from asgiref.sync import async_to_sync
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.exceptions import NotFound

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


def dispatch_selector_for_spec(
    view: Any,
    spec: SelectorSpec[Any, Any],
    *,
    extra_url_kwargs: dict[str, Any] | None = None,
) -> Any:
    """End-to-end dispatch for one ``SelectorSpec`` call.

    Runs the kwargs-resolution chain (``spec.kwargs`` →
    ``get_<action>_selector_kwargs`` → ``get_selector_kwargs``), filters
    the resulting pool against the selector's signature, and invokes it
    sync-or-async. Used by both selector viewset mixins and the standalone
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
    return run_selector(selector, resolve_callable_kwargs(selector, pool))


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
    """
    try:
        instance = dispatch_selector_for_spec(view, spec, extra_url_kwargs=extra_url_kwargs)
    except ObjectDoesNotExist as exc:
        raise NotFound() from exc
    if instance is None:
        raise NotFound()
    return instance
