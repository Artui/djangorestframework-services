"""``call_service`` — invoke a service callable from inside an HTTP request."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from asgiref.sync import async_to_sync
from rest_framework.request import Request

from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.is_async import is_async
from rest_framework_services.types.unset import UNSET
from rest_framework_services.views.mutation.map_service_error import map_service_error
from rest_framework_services.views.utils import resolve_callable_kwargs

ResultT = TypeVar("ResultT")


def call_service(
    service: Callable[..., ResultT],
    *,
    request: Request,
    data: Any = UNSET,
    instance: Any = UNSET,
    map_errors: bool = False,
    **extras: Any,
) -> ResultT:
    """Invoke ``service`` with the framework's kwargs pool.

    For a view, middleware, or custom action delegating to a service wired to a
    different action: the helper builds the pool the framework would build,
    filters it against the service's signature, and dispatches sync-or-async so
    the caller need not know which — async services are bridged through
    ``async_to_sync``, sync services called inline. Outside HTTP scope (Celery
    tasks, management commands) call the service directly with whatever kwargs
    you have; this helper is not the right tool there.

    Args:
        service: The service callable, sync or async.
        request: Required — the helper is HTTP-scoped by design. ``user`` is
            derived from ``request.user`` (``None`` if the request bypassed
            authentication middleware), matching the framework's own pool
            construction.
        data: Passed into the pool when not ``UNSET``; omitting it along with
            ``instance`` mirrors the create / list call shape.
        instance: Passed into the pool when not ``UNSET``.
        map_errors: Translate a raised
            :class:`~rest_framework_services.exceptions.service_error.ServiceError`
            into the DRF exception the normal view path raises for it
            (``ServiceValidationError`` → 400, any other → 422) so DRF's
            handler renders it as a proper response. Left ``False`` it
            propagates unchanged and an unhandled one surfaces as a ``500``.
        **extras: Merged into the pool; the signature filter
            (:func:`resolve_callable_kwargs`) decides which keys reach the
            service.
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
    try:
        if is_async(service):
            async_service = cast("Callable[..., Awaitable[ResultT]]", service)
            return async_to_sync(async_service)(**kwargs)
        return service(**kwargs)
    except ServiceError as exc:
        if map_errors:
            raise map_service_error(exc) from exc
        raise
