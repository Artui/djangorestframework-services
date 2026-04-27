"""Detect whether a callable is async."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any


def is_async(fn: Callable[..., Any]) -> bool:
    """Return True if calling ``fn(...)`` produces a coroutine.

    Handles plain ``async def`` functions, ``functools.partial`` wrapping
    one (via ``inspect.iscoroutinefunction``'s built-in unwrapping), and
    any callable whose ``__call__`` is a coroutine function.
    """
    if inspect.iscoroutinefunction(fn):
        return True
    # Inspect the class-level ``__call__`` rather than the bound instance
    # method so we can detect classes whose ``__call__`` is ``async def``.
    # ``B004`` doesn't apply: we're not using this to test callability.
    cls_call: Any = getattr(type(fn), "__call__", None)  # noqa: B004
    return cls_call is not None and inspect.iscoroutinefunction(cls_call)
