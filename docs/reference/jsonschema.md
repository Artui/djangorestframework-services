# JSON Schema

View-free JSON Schema generation. These helpers turn a `ServiceSpec` /
`SelectorSpec` (or a bare DRF serializer / dataclass) into a JSON Schema
dict, with **no** view, request, or `drf-spectacular` dependency — what an
alternate transport (a Pydantic-AI toolset, the MCP server) builds tool
definitions from. Distinct from the [OpenAPI](openapi.md) adapter, which
produces DRF serializer classes for DRF's own OpenAPI generators.

## `serializer_to_json_schema`

::: rest_framework_services.jsonschema.serializer_to_json_schema.serializer_to_json_schema

## `output_to_json_schema`

::: rest_framework_services.jsonschema.output_to_json_schema.output_to_json_schema

## `filterset_to_json_schema`

::: rest_framework_services.jsonschema.filterset_to_json_schema.filterset_to_json_schema

## `spec_to_json_schema`

::: rest_framework_services.jsonschema.spec_to_json_schema.spec_to_json_schema

## `JsonSchemaRegistry`

::: rest_framework_services.types.json_schema_registry.JsonSchemaRegistry

## `DEFAULT_JSON_SCHEMA_REGISTRY`

::: rest_framework_services.types.json_schema_registry.DEFAULT_JSON_SCHEMA_REGISTRY
