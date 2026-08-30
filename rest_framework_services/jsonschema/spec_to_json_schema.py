"""``spec_to_json_schema`` — derive a JSON Schema straight from a spec."""

from __future__ import annotations

from typing import Any, Literal

from rest_framework_services.jsonschema.filterset_to_json_schema import filterset_to_json_schema
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.jsonschema.serializer_to_json_schema import serializer_to_json_schema
from rest_framework_services.jsonschema.utils import callable_input_schema
from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

# Pool seeds the transport injects rather than the caller supplying them, so
# they are skipped when reflecting a selector's parameters. A param filled by a
# ``spec.kwargs`` provider can't be skipped statically (a callable, not a known
# key set) and is surfaced anyway — harmless, since every reflected property is
# optional.
_SELECTOR_SEED_PARAMS: frozenset[str] = frozenset({"request", "user", "view"})


def spec_to_json_schema(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    *,
    phase: Literal["input", "output"] = "input",
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
    max_depth: int | None = None,
) -> dict[str, Any] | None:
    """Derive a JSON Schema from a spec, reading the right serializer off it.

    The convenience an alternate transport (a Pydantic-AI toolset, the MCP server) calls
    instead of reaching into spec internals itself. ``registry`` supplies consumer rules
    for custom field / filter / Python types — see
    [`JsonSchemaRegistry`][rest_framework_services.types.json_schema_registry.JsonSchemaRegistry].

    ``phase="input"`` (default) returns the input-argument schema:

    - [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] → its
        ``input_serializer`` (``spec.partial`` honoured).
    - [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] → an
      object whose ``properties`` combine the selector callable's own annotated
      parameters (skipping the ``request`` / ``user`` / ``view`` transport seeds) with
      its ``filter_set`` fields, so ``get_widget(user, pk)`` advertises ``pk`` instead
      of leaning on its docstring; a bare ``{"type": "object"}`` when it exposes
      neither. A ``**kwargs: Unpack[SomeExtras]`` parameter is **expanded** into one
      property per ``TypedDict`` key, its required keys populating ``required``, so a
      URL kwarg read from ``extras`` is discoverable off-HTTP rather than a hidden
      ``KeyError``. Introspecting a ``filter_set`` needs the ``[filter]`` extra.

    ``phase="output"`` returns the output schema, or ``None`` when undeclared: a
    [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] supplies its
    ``output_selector_spec``'s ``output_serializer`` and ``kind``, a
    [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] its own.

    ``max_depth`` bounds how many serializer levels are described, truncating
    deeper ones to ``{"type": "object"}``; ``None``, the default, describes them
    all. It reaches the serializer-backed schemas — a ``ServiceSpec``'s input
    and either spec's output — and has nothing to bound on a ``SelectorSpec``'s
    input, which is reflected from a callable and a ``filter_set`` rather than
    walked. A serializer that nests itself is truncated at the re-entry
    regardless, because the alternative is a ``RecursionError`` raised while a
    transport declares its tools.
    """
    if phase == "input":
        return _input_schema(spec, registry, max_depth)
    return _output_schema(spec, registry, max_depth)


def _input_schema(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    registry: JsonSchemaRegistry,
    max_depth: int | None,
) -> dict[str, Any]:
    if isinstance(spec, ServiceSpec):
        return serializer_to_json_schema(
            spec.input_serializer,
            partial=bool(spec.partial),
            registry=registry,
            max_depth=max_depth,
        )
    schema: dict[str, Any] = {"type": "object"}
    properties: dict[str, Any] = {}
    required: list[str] = []
    if spec.selector is not None:
        callable_props, callable_required = callable_input_schema(
            spec.selector, skip=_SELECTOR_SEED_PARAMS, registry=registry
        )
        properties.update(callable_props)
        required.extend(callable_required)
    if spec.filter_set is not None:
        # A declared filter_set field is the more precise source for a shared
        # name, so it wins over a bare callable parameter of the same name.
        properties.update(filterset_to_json_schema(spec.filter_set, registry=registry))
    if properties:
        schema["properties"] = properties
    if required:
        # Only ``Unpack[TypedDict]`` extras contribute requiredness; dedupe
        # defensively.
        schema["required"] = list(dict.fromkeys(required))
    return schema


def _output_schema(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    registry: JsonSchemaRegistry,
    max_depth: int | None,
) -> dict[str, Any] | None:
    if isinstance(spec, ServiceSpec):
        nested = spec.output_selector_spec
        if nested is None:
            return None
        return output_to_json_schema(
            nested.output_serializer, kind=nested.kind, registry=registry, max_depth=max_depth
        )
    return output_to_json_schema(
        spec.output_serializer, kind=spec.kind, registry=registry, max_depth=max_depth
    )
