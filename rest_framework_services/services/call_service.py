"""``call_service`` — invoke a service callable from inside an HTTP request.

Designed for the case where one view (or middleware, or custom action)
needs to delegate to a service originally wired to a different action.
The helper builds the same kwargs pool the framework would build, filters
it against the service's signature, and dispatches sync-or-async — the
caller doesn't have to know which.

Outside HTTP scope (Celery tasks, management commands), call the service
callable directly with whatever kwargs you have; this helper is not the
right tool there.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from asgiref.sync import async_to_sync
from rest_framework.request import Request

from rest_framework_services._compat.is_async import is_async
from rest_framework_services.types.unset import UNSET
from rest_framework_services.views.utils import resolve_callable_kwargs

ResultT = TypeVar("ResultT")


def call_service(
    service: Callable[..., ResultT],
    *,
    request: Request,
    data: Any = UNSET,
    instance: Any = UNSET,
    **extras: Any,
) -> ResultT:
    """Invoke ``service`` with the framework's kwargs pool.

    ``request`` is required — the helper is HTTP-scoped by design. ``user``
    is derived from ``request.user`` (``None`` if the request bypassed
    authentication middleware), matching the framework's own pool
    construction.

    ``data`` and ``instance`` are passed through when not ``UNSET``;
    omitting them mirrors the create / list call shape. Anything else
    goes into ``**extras`` and merges into the pool — the framework's
    standard signature filter (:func:`resolve_callable_kwargs`) decides
    which keys actually reach the service.

    Async services are bridged transparently via ``async_to_sync``;
    sync services are called inline.
    """
    pool: dict[str, Any] = {
        "request": request,
        "user": getattr(request, "user", None),
    }
    if data is not UNSET:
        pool["data"] = data
    if instance is not UNSET:
        pool["instance"] = instance
    pool.update(extras)
    kwargs = resolve_callable_kwargs(service, pool)
    if is_async(service):
        async_service = cast("Callable[..., Awaitable[ResultT]]", service)
        return async_to_sync(async_service)(**kwargs)
    return service(**kwargs)
