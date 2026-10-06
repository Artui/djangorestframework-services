"""A decorator for ``test_provider_keys``, kept in a module of its own on purpose.

The wrapper ``passthrough`` returns carries the provider's annotations, copied by
``functools.wraps``, but its ``__globals__`` are this module's, where none of the
``TypedDict`` classes those annotations name exist. So a reader that resolved the
return annotation in the wrapper's globals, rather than the decorated function's,
would find nothing there.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any


def passthrough(provider: Callable[..., Any]) -> Callable[..., Any]:
    """``provider`` behind a wrapper defined here, as a decorator from another package would be."""

    @functools.wraps(provider)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return provider(*args, **kwargs)

    return wrapper
