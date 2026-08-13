"""Asynchronous selector protocol."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AsyncSelector(Protocol):
    """Async sibling of [`Selector`][rest_framework_services.selectors.selector.Selector]."""

    async def __call__(self, *args: Any, **kwargs: Any) -> Any: ...
