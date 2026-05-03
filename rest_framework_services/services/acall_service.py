"""``acall_service`` — async counterpart to :func:`call_service`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from rest_framework.request import Request

from rest_framework_services._compat.is_async import is_async
from rest_framework_services.types.unset import UNSET
from rest_framework_services.views.utils import resolve_callable_kwargs

ResultT = TypeVar("ResultT")


async def acall_service(
    service: Callable[..., ResultT] | Callable[..., Awaitable[ResultT]],
    *,
    request: Request,
    data: Any = UNSET,
    instance: Any = UNSET,
    **extras: Any,
) -> ResultT:
    """Invoke ``service`` from async code with the framework's kwargs pool.

    Same contract as :func:`call_service`. Async services are awaited
    directly; sync services are called inline (no thread hop) — caller is
    responsible for any sync-side I/O safety.
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
        return await async_service(**kwargs)
    sync_service = cast("Callable[..., ResultT]", service)
    return sync_service(**kwargs)
