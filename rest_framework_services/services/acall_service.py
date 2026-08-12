"""``acall_service`` — async counterpart to :func:`call_service`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from rest_framework.request import Request

from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.is_async import is_async
from rest_framework_services.types.unset import UNSET
from rest_framework_services.views.mutation.map_service_error import map_service_error
from rest_framework_services.views.utils import resolve_callable_kwargs

ResultT = TypeVar("ResultT")


async def acall_service(
    service: Callable[..., ResultT] | Callable[..., Awaitable[ResultT]],
    *,
    request: Request,
    data: Any = UNSET,
    instance: Any = UNSET,
    map_errors: bool = False,
    **extras: Any,
) -> ResultT:
    """Invoke ``service`` from async code with the framework's kwargs pool.

    Same contract as :func:`call_service`, ``map_errors`` included. Async
    services are awaited directly; a sync service is called inline with no
    thread hop, so the caller owns any sync-side I/O safety.
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
            return await async_service(**kwargs)
        sync_service = cast("Callable[..., ResultT]", service)
        return sync_service(**kwargs)
    except ServiceError as exc:
        if map_errors:
            raise map_service_error(exc) from exc
        raise
