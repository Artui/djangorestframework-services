"""``adelete_model`` — async default delete-action service factory."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from rest_framework_services.mutations.utils import adelete_children
from rest_framework_services.types.change_result import ModelT
from rest_framework_services.types.child_spec import ChildSpec


def adelete_model(
    model: type[ModelT],
    *,
    soft_delete: Callable[[ModelT], Awaitable[None]] | None = None,
    children: Mapping[str, ChildSpec] | None = None,
) -> Callable[..., Awaitable[None]]:
    """Async sibling of
    :func:`~rest_framework_services.services.delete_model`.

    Calls ``await instance.adelete()`` by default (Django 4.1+; the
    package floor is 4.2, so this is always available). ``soft_delete``
    is an optional ``async`` hook called instead of ``adelete``. ``children``
    removes the declared reverse-FK collections first, with the rest of the
    kwargs pool handed on as ``context=`` (see
    :func:`~rest_framework_services.services.delete_model`).
    """

    async def _service(*, instance: ModelT, **kwargs: Any) -> None:
        if children is not None:
            await adelete_children(instance, children, context=kwargs)
        if soft_delete is not None:
            await soft_delete(instance)
        else:
            await instance.adelete()
        return None

    return _service
