"""``spec_to_json_schema`` — derive a JSON Schema straight from a spec."""

from __future__ import annotations

from typing import Any, Literal

from rest_framework_services.jsonschema.filterset_to_json_schema import filterset_to_json_schema
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.jsonschema.serializer_to_json_schema import serializer_to_json_schema
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


def spec_to_json_schema(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    *,
    phase: Literal["input", "output"] = "input",
) -> dict[str, Any] | None:
    """Derive a JSON Schema from a spec, reading the right serializer off it.

    The convenience an alternate transport (a Pydantic-AI toolset, the MCP
    server) calls instead of reaching into spec internals itself.

    ``phase="input"`` (default) returns the input-argument schema:

    - :class:`ServiceSpec` → its ``input_serializer`` (``spec.partial`` honoured).
    - :class:`SelectorSpec` → an object whose ``properties`` are the selector's
      ``filter_set`` fields (via ``filterset_to_json_schema``), or a bare
      ``{"type": "object"}`` when it declares no ``filter_set``. (Introspecting a
      ``filter_set`` needs the ``[filter]`` extra; a selector without one stays
      dependency-free.)

    ``phase="output"`` returns the output schema, or ``None`` when undeclared:

    - :class:`ServiceSpec` → its ``output_selector_spec``'s ``output_serializer``
      and ``kind``.
    - :class:`SelectorSpec` → its own ``output_serializer`` and ``kind``.
    """
    if phase == "input":
        return _input_schema(spec)
    return _output_schema(spec)


def _input_schema(spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]) -> dict[str, Any]:
    if isinstance(spec, ServiceSpec):
        return serializer_to_json_schema(spec.input_serializer, partial=bool(spec.partial))
    schema: dict[str, Any] = {"type": "object"}
    if spec.filter_set is not None:
        properties = filterset_to_json_schema(spec.filter_set)
        if properties:
            schema["properties"] = properties
    return schema


def _output_schema(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
) -> dict[str, Any] | None:
    if isinstance(spec, ServiceSpec):
        nested = spec.output_selector_spec
        if nested is None:
            return None
        return output_to_json_schema(nested.output_serializer, kind=nested.kind)
    return output_to_json_schema(spec.output_serializer, kind=spec.kind)
