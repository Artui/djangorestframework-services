"""``acreate_model`` — async default create-action service factory."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from rest_framework_services.mutations.acreate_from_input import acreate_from_input
from rest_framework_services.services._resolve_m2m import resolve_m2m
from rest_framework_services.types.change_result import ModelT
from rest_framework_services.types.child_spec import ChildSpec


def acreate_model(
    model: type[ModelT],
    *,
    field_map: dict[str, str] | None = None,
    exclude_fields: list[str] | None = None,
    m2m: Mapping[str, Any] | Callable[[Any], Mapping[str, Any]] | None = None,
    children: Mapping[str, ChildSpec] | None = None,
) -> Callable[..., Awaitable[ModelT]]:
    """Async sibling of
    :func:`~rest_framework_services.services.create_model`.

    Returns an ``async def`` closure that wraps
    :func:`~rest_framework_services.mutations.acreate_from_input`. The
    framework's :func:`~rest_framework_services.is_async.is_async`
    detection routes it through the async dispatch path automatically.
    ``children`` is forwarded for declarative reverse-FK writes (see
    :func:`~rest_framework_services.services.create_model`).
    """

    async def _service(*, data: Any, **kwargs: Any) -> ModelT:
        result = await acreate_from_input(
            model,
            data,
            field_map=field_map,
            exclude_fields=exclude_fields,
            m2m=resolve_m2m(m2m, data),
            children=children,
        )
        return result.instance

    return _service
