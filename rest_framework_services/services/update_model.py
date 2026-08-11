"""``update_model`` — default update-action service factory."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from rest_framework_services.mutations.update_from_input import update_from_input
from rest_framework_services.services._resolve_m2m import resolve_m2m
from rest_framework_services.types.change_result import ModelT
from rest_framework_services.types.child_spec import ChildSpec


def update_model(
    model: type[ModelT],
    *,
    field_map: dict[str, str] | None = None,
    exclude_fields: list[str] | None = None,
    m2m: Mapping[str, Any] | Callable[[Any], Mapping[str, Any]] | None = None,
    update_fields: bool | list[str] = True,
    children: Mapping[str, ChildSpec] | None = None,
) -> Callable[..., ModelT]:
    """Return a service callable that updates the resolved instance in place.

    Equivalent to::

        def update_author(*, instance: Author, data: AuthorIn, **_: Any) -> Author:
            return update_from_input(instance, data).instance

    ``model`` is accepted for symmetry with
    :func:`create_model` / :func:`delete_model` and to bind ``ModelT`` for
    the type checker; the instance itself comes from the view's
    ``get_object()``. ``field_map``, ``exclude_fields``, ``m2m``,
    ``update_fields``, and ``children`` are forwarded to
    :func:`~rest_framework_services.mutations.update_from_input`. ``m2m``
    accepts either a static mapping or a callable receiving the validated
    ``data`` (see :func:`create_model` for the common shape); ``children``
    reconciles reverse-FK collections from ``data[relation]`` per its
    :class:`~rest_framework_services.ChildSpec`.

    The rest of the framework's kwargs pool is handed on as the helper's
    ``context=``, so a per-child service declared on a ``ChildSpec`` can see
    who is calling (see :func:`create_model`).
    """

    def _service(*, instance: ModelT, data: Any, **kwargs: Any) -> ModelT:
        return update_from_input(
            instance,
            data,
            field_map=field_map,
            exclude_fields=exclude_fields,
            m2m=resolve_m2m(m2m, data),
            update_fields=update_fields,
            children=children,
            context=kwargs,
        ).instance

    return _service
