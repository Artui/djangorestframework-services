"""``acall_selector`` — async counterpart to [`call_selector`][rest_framework_services.selectors.call_selector.call_selector]."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from rest_framework.request import Request

from rest_framework_services.is_async import is_async
from rest_framework_services.views.utils import resolve_callable_kwargs

ResultT = TypeVar("ResultT")


async def acall_selector(
    selector: Callable[..., ResultT] | Callable[..., Awaitable[ResultT]],
    *,
    request: Request,
    **extras: Any,
) -> ResultT:
    """Invoke ``selector`` from async code with the framework's kwargs pool.

    Async selectors are awaited; sync selectors are called inline.
    """
    pool: dict[str, Any] = {
        "request": request,
        "user": getattr(request, "user", None),
        **extras,
    }
    kwargs = resolve_callable_kwargs(selector, pool)
    if is_async(selector):
        async_selector = cast("Callable[..., Awaitable[ResultT]]", selector)
        return await async_selector(**kwargs)
    sync_selector = cast("Callable[..., ResultT]", selector)
    return sync_selector(**kwargs)
