"""``aupdate_model`` — async default update-action service factory."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from rest_framework_services.mutations.aupdate_from_input import aupdate_from_input
from rest_framework_services.services._resolve_m2m import resolve_m2m
from rest_framework_services.types.change_result import ModelT
from rest_framework_services.types.child_spec import ChildSpec


def aupdate_model(
    model: type[ModelT],
    *,
    field_map: dict[str, str] | None = None,
    exclude_fields: list[str] | None = None,
    m2m: Mapping[str, Any] | Callable[[Any], Mapping[str, Any]] | None = None,
    update_fields: bool | list[str] = True,
    children: Mapping[str, ChildSpec] | None = None,
) -> Callable[..., Awaitable[ModelT]]:
    """Async sibling of
    :func:`~rest_framework_services.services.update_model`."""

    async def _service(*, instance: ModelT, data: Any, **kwargs: Any) -> ModelT:
        result = await aupdate_from_input(
            instance,
            data,
            field_map=field_map,
            exclude_fields=exclude_fields,
            m2m=resolve_m2m(m2m, data),
            update_fields=update_fields,
            children=children,
            context=kwargs,
        )
        return result.instance

    return _service
