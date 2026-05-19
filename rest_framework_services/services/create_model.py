"""``create_model`` — default create-action service factory."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from rest_framework_services.mutations.create_from_input import create_from_input
from rest_framework_services.services._resolve_m2m import resolve_m2m
from rest_framework_services.types.change_result import ModelT


def create_model(
    model: type[ModelT],
    *,
    field_map: dict[str, str] | None = None,
    exclude_fields: list[str] | None = None,
    m2m: Mapping[str, Any] | Callable[[Any], Mapping[str, Any]] | None = None,
) -> Callable[..., ModelT]:
    """Return a service callable that builds ``model`` from validated input.

    Equivalent to writing the canonical glue stub by hand::

        def create_author(*, data: AuthorIn, **_: Any) -> Author:
            return create_from_input(Author, data).instance

    ``field_map`` and ``exclude_fields`` are forwarded to
    :func:`~rest_framework_services.mutations.create_from_input`.

    ``m2m`` accepts either a static mapping (passed straight through) or a
    callable that receives the validated ``data`` and returns the mapping —
    the common case where M2M values live on the input dataclass / dict
    itself::

        create_model(
            Post,
            m2m=lambda data: {"tags": data.tags},
        )

    The returned closure accepts ``**kwargs`` so the framework's kwargs pool
    (``request``, ``user``, URL kwargs, ``ServiceSpec.kwargs`` returns) is
    absorbed without the service caring — matching the unified
    :class:`~rest_framework_services.services.CreateService` Protocol's
    default ``ExtraT`` (open extras).
    """

    def _service(*, data: Any, **kwargs: Any) -> ModelT:
        return create_from_input(
            model,
            data,
            field_map=field_map,
            exclude_fields=exclude_fields,
            m2m=resolve_m2m(m2m, data),
        ).instance

    return _service
