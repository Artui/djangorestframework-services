"""``run_service`` — synchronous service dispatch with optional atomic wrapping.

Part of the **stable dispatch surface** (see the dispatch reference page):
the primitive alternate transports (e.g. ``djangorestframework-mcp-server``)
build on instead of re-implementing the "how to call a service" rules.
Lived in the private ``_compat`` package before 0.17.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from asgiref.sync import async_to_sync
from django.db import transaction

from rest_framework_services.is_async import is_async
from rest_framework_services.services.arun_service import arun_service


def run_service(
    fn: Callable[..., Any],
    kwargs: dict[str, Any],
    *,
    atomic: bool,
) -> Any:
    """Call ``fn(**kwargs)`` from sync code, optionally inside ``transaction.atomic()``.

    An ``async def`` service is bridged transparently via ``async_to_sync``,
    mirroring ``run_selector``. The
    bridge is **not** optional politeness: without it an async service returns its
    coroutine object to the caller un-awaited — no exception, and under
    ``atomic=True`` the transaction commits before the body would have run. The
    HTTP path always bridged (its own ``dispatch_service`` wrapper did this);
    ``dispatch_spec`` reached this leaf directly, so the same spec resolved over
    HTTP and returned a coroutine over MCP.

    Async services under ``atomic=True`` route through [`arun_service`][rest_framework_services.services.arun_service.arun_service], which
    owns the thread-sensitivity rule that keeps the ORM connection holding the
    open transaction the same one the inner async DB calls use.
    """
    if is_async(fn):
        return async_to_sync(arun_service)(fn, kwargs, atomic=atomic)
    if atomic:
        with transaction.atomic():
            return fn(**kwargs)
    return fn(**kwargs)


__all__ = ["run_service"]
