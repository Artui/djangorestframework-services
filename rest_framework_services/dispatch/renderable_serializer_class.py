"""``renderable_serializer_class`` — the class an output declaration renders through."""

from __future__ import annotations

import functools
from dataclasses import is_dataclass
from typing import Any, cast, overload

from rest_framework.serializers import BaseSerializer
from rest_framework_dataclasses.serializers import DataclassSerializer


@overload
def renderable_serializer_class(declared: None) -> None: ...


@overload
def renderable_serializer_class(declared: type) -> type[BaseSerializer[Any]]: ...


def renderable_serializer_class(declared: type | None) -> type[BaseSerializer[Any]] | None:
    """The class an ``output_serializer`` declaration renders through.

    A bare dataclass type is wrapped in a ``DataclassSerializer`` — the payload
    [`output_to_json_schema`][rest_framework_services.jsonschema.output_to_json_schema.output_to_json_schema]
    already describes for it, field by field — and anything else comes back
    exactly as declared, ``None`` included. Instantiating the declaration directly
    works for a serializer and fails for a dataclass, whose own ``__init__`` is
    handed ``many=`` / ``context=``; resolving it here first is what lets both
    shapes render.

    Every render site in this package resolves through it: ``render_spec_output``,
    the selector views' and viewsets' ``get_serializer_class()``,
    ``@selector_action`` and a mutation's response. A transport that instantiates
    an output declaration itself — to render a resource outside
    ``render_spec_output``, say — calls this and instantiates what it returns:

        serializer_class = renderable_serializer_class(spec.output_serializer)
        payload = serializer_class(
            value, many=many, context=base_serializer_context(view=view, request=request)
        ).data

    A dataclass gets one class for the life of the process, so repeated calls
    return the same object.

    The pass-through is deliberate rather than a check. A ``BaseSerializer``
    subclass is the ordinary case, and a declaration that is neither is refused
    where its schema is derived; refusing it here as well would break a caller
    that renders through a serializer-shaped factory without ever deriving one.
    """
    # Both conjuncts are needed: ``is_dataclass`` is also true of a dataclass
    # *instance*, which is not a declaration this can wrap.
    if isinstance(declared, type) and is_dataclass(declared):
        return _dataclass_serializer_class(declared)
    return cast("type[BaseSerializer[Any]] | None", declared)


# Cached per dataclass type, so each dataclass renders through one class for the
# life of the process: the same class from every render site and from the OpenAPI
# coercion, and no class pair built per render. Building one costs about a
# quarter of rendering a small dataclass, and each is a reference cycle the
# collector has to find.
#
# That is module-level state, which this package otherwise refuses for its
# registries — and the reason does not apply here. A registry carries
# configuration two mounts may need to differ on; this carries none. The class
# holds only ``Meta.dataclass``, a pure function of the key, while
# ``DataclassSerializer`` builds its fields per instance and reads DRF settings
# as it does, so a cached class freezes no setting and cannot answer one mount
# or one test with another's configuration. What the cache does cost is a
# strong reference to every dataclass it has wrapped, which is bounded by the
# declared outputs.
#
# Private rather than exported next to the resolver: for a dataclass type the
# resolver returns exactly this class, so a second public name would be a second
# spelling of one answer, and one more thing a consumer could call with a
# declaration that is not a dataclass.
@functools.cache
def _dataclass_serializer_class(dataclass_type: type) -> type[DataclassSerializer[Any]]:
    return type(
        f"_AutoDataclassSerializer_{dataclass_type.__name__}",
        (DataclassSerializer,),
        {"Meta": type("Meta", (), {"dataclass": dataclass_type})},
    )


__all__ = ["renderable_serializer_class"]
