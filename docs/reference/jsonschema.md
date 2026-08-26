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

A selector's input schema reflects the **selector callable's own parameters** —
names plus a JSON type from each annotation, skipping the `request` / `user` /
`view` transport seeds — merged with its `filter_set` fields. So a lookup selector
like `get_widget(user, pk)` advertises `pk` instead of a bare `{"type": "object"}`
that leans on the docstring alone. An un-annotated parameter is still surfaced by
name (untyped `{}`); a `filter_set` field wins over a callable parameter of the
same name.

### What an annotation publishes

The mapping from a Python annotation is structural, so a declaration a caller
took the trouble to write survives into the published schema:

| Annotation | Schema |
|---|---|
| `str` / `int` / `float` / `bool` / `None` | the matching JSON type |
| `datetime` / `date` / `time` / `UUID` / `Decimal` | `string` with the format DRF's own field would use |
| `Literal["open", "closed"]`, an `Enum` subclass | `{"enum": [...]}` — by member *value* for an `Enum` |
| `list[X]`, `set[X]` / `frozenset[X]` | `array` (a set adds `uniqueItems`) |
| `dict[str, X]` | `object` with `additionalProperties` |
| `X \| None`, `Union[X, Y]` | `{"anyOf": [...]}` |

Anything else — a domain class, a `Callable`, a bare `Any`, an annotation that
could not be resolved — publishes as `{}`, which in JSON Schema means **any
value**, so a caller cannot tell it from a value that genuinely is
unconstrained. Register the types that matter to you rather than letting them
publish as anything:

```python
registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
    python_types=[(Money, {"type": "string", "format": "money"})],
)
```

Rules are matched by type *identity*, and members are resolved recursively, so
one rule for `Money` also covers `list[Money]` and `Money | None`.

## `JsonSchemaRegistry`

::: rest_framework_services.types.json_schema_registry.JsonSchemaRegistry

## `DEFAULT_JSON_SCHEMA_REGISTRY`

::: rest_framework_services.types.json_schema_registry.DEFAULT_JSON_SCHEMA_REGISTRY
