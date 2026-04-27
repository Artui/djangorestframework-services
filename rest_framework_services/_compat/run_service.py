"""Synchronous service dispatch with optional atomic wrapping."""

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
