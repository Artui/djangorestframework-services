"""``run_service`` — synchronous service dispatch with optional atomic wrapping.

Part of the **stable dispatch surface** (see the dispatch reference page):
the primitive alternate transports (e.g. ``djangorestframework-mcp-server``)
build on instead of re-implementing the "how to call a service" rules.
Promoted out of the private ``_compat`` package in 0.17.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.db import transaction


def run_service(
    fn: Callable[..., Any],
    kwargs: dict[str, Any],
    *,
    atomic: bool,
) -> Any:
    """Call ``fn(**kwargs)``, optionally inside ``transaction.atomic()``."""
    if atomic:
        with transaction.atomic():
            return fn(**kwargs)
    return fn(**kwargs)


__all__ = ["run_service"]
