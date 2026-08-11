"""``adelete_model`` — async default delete-action service factory."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from rest_framework_services.mutations.utils import adelete_relations, merge_relations
from rest_framework_services.types.change_result import ModelT
from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.relation_spec import RelationSpec


def adelete_model(
    model: type[ModelT],
    *,
    soft_delete: Callable[[ModelT], Awaitable[None]] | None = None,
    children: Mapping[str, ChildSpec] | None = None,
    relations: Mapping[str, RelationSpec] | None = None,
) -> Callable[..., Awaitable[None]]:
    """Async sibling of
    :func:`~rest_framework_services.services.delete_model`.

    Calls ``await instance.adelete()`` by default (Django 4.1+; the
    package floor is 4.2, so this is always available). ``soft_delete``
    is an optional ``async`` hook called instead of ``adelete``.
    ``relations`` (and its reverse-FK alias ``children``) removes what the
    parent owns first, by the one rule
    :func:`~rest_framework_services.services.delete_model` states, with the
    rest of the kwargs pool handed on as ``context=``.
    """
    declared = merge_relations(children, relations)

    async def _service(*, instance: ModelT, **kwargs: Any) -> None:
        if declared:
            await adelete_relations(instance, declared, context=kwargs)
        if soft_delete is not None:
            await soft_delete(instance)
        else:
            await instance.adelete()
        return None

    return _service
