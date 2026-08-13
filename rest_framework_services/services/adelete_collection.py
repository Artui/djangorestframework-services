"""``adelete_collection`` — async default bulk-delete service over a collection."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from rest_framework_services.types.change_result import ModelT


def adelete_collection(
    model: type[ModelT],
    *,
    soft_delete: Callable[[Any], Awaitable[None]] | None = None,
) -> Callable[..., Awaitable[None]]:
    """Async sibling of
    [`delete_collection`][rest_framework_services.services.delete_collection.delete_collection].

    Calls ``await collection.adelete()`` by default (Django 4.1+; the package
    floor is 4.2). ``soft_delete`` is an optional ``async`` hook called with the
    ``collection`` instead.
    """

    async def _service(*, collection: Any, **kwargs: Any) -> None:
        if soft_delete is not None:
            await soft_delete(collection)
        else:
            await collection.adelete()
        return None

    return _service


__all__ = ["adelete_collection"]
