"""``create_model`` — default create-action service factory."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from rest_framework_services.mutations.create_from_input import create_from_input
from rest_framework_services.services._resolve_m2m import resolve_m2m
from rest_framework_services.types.change_result import ModelT
from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.relation_spec import RelationSpec


def create_model(
    model: type[ModelT],
    *,
    field_map: dict[str, str] | None = None,
    exclude_fields: list[str] | None = None,
    m2m: Mapping[str, Any] | Callable[[Any], Mapping[str, Any]] | None = None,
    children: Mapping[str, ChildSpec] | None = None,
    relations: Mapping[str, RelationSpec] | None = None,
) -> Callable[..., ModelT]:
    """Return a service callable that builds ``model`` from validated input.

    Equivalent to writing the canonical glue stub by hand:

        def create_author(*, data: AuthorIn, **_: Any) -> Author:
            return create_from_input(Author, data).instance

    ``field_map`` and ``exclude_fields`` are forwarded to
    [`create_from_input`][rest_framework_services.mutations.create_from_input.create_from_input].

    ``m2m`` accepts either a static mapping (passed straight through) or a
    callable that receives the validated ``data`` and returns the mapping —
    the common case where M2M values live on the input dataclass / dict
    itself:

        create_model(
            Post,
            m2m=lambda data: {"tags": data.tags},
        )

    ``relations`` (and its reverse-FK alias ``children``) is forwarded to
    [`create_from_input`][rest_framework_services.mutations.create_from_input.create_from_input] to write
    nested relations declaratively (no hand-written service):

        create_model(
            Author,
            relations={"books": ChildSpec(model=Book, fk="author")},
        )

    The returned closure accepts ``**kwargs`` so the framework's kwargs pool
    (``request``, ``user``, URL kwargs, ``ServiceSpec.kwargs`` returns) is absorbed
    without the service caring — matching the unified
    [`CreateService`][rest_framework_services.services.create_service.CreateService]
    Protocol's default ``ExtraT`` (open extras). That same pool is handed on as the
    helper's ``context=``, so a per-child service declared on a
    [`ChildSpec`][rest_framework_services.types.child_spec.ChildSpec] can see who is
    calling."""

    def _service(*, data: Any, **kwargs: Any) -> ModelT:
        return create_from_input(
            model,
            data,
            field_map=field_map,
            exclude_fields=exclude_fields,
            m2m=resolve_m2m(m2m, data),
            children=children,
            relations=relations,
            context=kwargs,
        ).instance

    return _service
