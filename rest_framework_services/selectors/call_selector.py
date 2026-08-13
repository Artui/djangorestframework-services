"""``call_selector`` — invoke a selector callable from inside an HTTP request.

Mirrors [`call_service`][rest_framework_services.services.call_service.call_service]
for selectors. No ``data`` / ``instance`` keys — selectors are read-only
business logic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from asgiref.sync import async_to_sync
from rest_framework.request import Request

from rest_framework_services.is_async import is_async
from rest_framework_services.views.utils import resolve_callable_kwargs

ResultT = TypeVar("ResultT")


def call_selector(
    selector: Callable[..., ResultT],
    *,
    request: Request,
    **extras: Any,
) -> ResultT:
    """Invoke ``selector`` with the framework's kwargs pool.

    ``request`` is required; ``user`` is derived from ``request.user``.
    Async selectors are bridged via ``async_to_sync``.
    """
    pool: dict[str, Any] = {
        "request": request,
        "user": getattr(request, "user", None),
        **extras,
    }
    kwargs = resolve_callable_kwargs(selector, pool)
    if is_async(selector):
        async_selector = cast("Callable[..., Awaitable[ResultT]]", selector)
        return async_to_sync(async_selector)(**kwargs)
    return selector(**kwargs)
