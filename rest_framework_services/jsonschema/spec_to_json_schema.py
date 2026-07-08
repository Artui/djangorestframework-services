"""``spec_to_json_schema`` — derive a JSON Schema straight from a spec."""

from __future__ import annotations

from typing import Any, Literal

from rest_framework_services.jsonschema.filterset_to_json_schema import filterset_to_json_schema
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.jsonschema.serializer_to_json_schema import serializer_to_json_schema
from rest_framework_services.jsonschema.utils import callable_input_properties
from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

# Pool seeds a selector callable receives that the caller never supplies — the
# transport injects them (see the selector dispatch pool). Skipped when
# reflecting the callable's parameters into its input schema. (Params filled by a
# ``spec.kwargs`` provider can't be skipped statically — it's a callable, not a
# known key set — so a kwargs-provided param is surfaced; harmless, as every
# reflected property is optional.)
_SELECTOR_SEED_PARAMS: frozenset[str] = frozenset({"request", "user", "view"})


def spec_to_json_schema(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    *,
    phase: Literal["input", "output"] = "input",
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
) -> dict[str, Any] | None:
    """Derive a JSON Schema from a spec, reading the right serializer off it.

    The convenience an alternate transport (a Pydantic-AI toolset, the MCP
    server) calls instead of reaching into spec internals itself.

    ``phase="input"`` (default) returns the input-argument schema:

    - :class:`ServiceSpec` → its ``input_serializer`` (``spec.partial`` honoured).
    - :class:`SelectorSpec` → an object whose ``properties`` combine the selector
      callable's own parameters (names → type from their annotations, skipping the
      ``request`` / ``user`` / ``view`` transport seeds) with its ``filter_set``
      fields (via ``filterset_to_json_schema``); a bare ``{"type": "object"}`` when
      it exposes neither. So a retrieve selector like ``get_widget(user, pk)`` now
      advertises ``pk`` instead of leaning entirely on its docstring. (Introspecting
      a ``filter_set`` needs the ``[filter]`` extra; a selector without one stays
      dependency-free.)

    ``phase="output"`` returns the output schema, or ``None`` when undeclared:

    - :class:`ServiceSpec` → its ``output_selector_spec``'s ``output_serializer``
      and ``kind``.
    - :class:`SelectorSpec` → its own ``output_serializer`` and ``kind``.

    ``registry`` supplies consumer rules for custom field / filter / Python types
    — see :class:`~rest_framework_services.JsonSchemaRegistry`.
    """
    if phase == "input":
        return _input_schema(spec, registry)
    return _output_schema(spec, registry)


def _input_schema(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any], registry: JsonSchemaRegistry
) -> dict[str, Any]:
    if isinstance(spec, ServiceSpec):
        return serializer_to_json_schema(
            spec.input_serializer, partial=bool(spec.partial), registry=registry
        )
    schema: dict[str, Any] = {"type": "object"}
    properties: dict[str, Any] = {}
    if spec.selector is not None:
        callable_props = callable_input_properties(
            spec.selector, skip=_SELECTOR_SEED_PARAMS, registry=registry
        )
        properties.update(callable_props)
    if spec.filter_set is not None:
        # A declared filter_set field is the more precise source for a shared
        # name, so it wins over a bare callable parameter of the same name.
        properties.update(filterset_to_json_schema(spec.filter_set, registry=registry))
    if properties:
        schema["properties"] = properties
    return schema


def _output_schema(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any], registry: JsonSchemaRegistry
) -> dict[str, Any] | None:
    if isinstance(spec, ServiceSpec):
        nested = spec.output_selector_spec
        if nested is None:
            return None
        return output_to_json_schema(nested.output_serializer, kind=nested.kind, registry=registry)
    return output_to_json_schema(spec.output_serializer, kind=spec.kind, registry=registry)
