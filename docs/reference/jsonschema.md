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

### What a filter publishes about itself

A filter carries more than a type. Its `label` becomes `title`, its `help_text`
becomes `description`, and a `ChoiceFilter`'s labels ride along with their
constants as `{"const": ..., "title": ...}` — the same shape the serializer path
has always produced, so one project's constants are described one way whichever
side of a spec they arrive on. Labels that only restate their value are dropped.

Where the argument's own name does not give the lookup away, it is stated:

```python
class ArticleFilter(django_filters.FilterSet):
    min_views = django_filters.NumberFilter(field_name="views", lookup_expr="gte")
```

publishes `min_views` as
`{"type": "number", "description": "Matches `views` with the `gte` lookup."}`.
A filter whose name, field and lookup already agree — `name` matching `name` for
equality — says nothing extra, and your own `help_text` always wins over the
derived wording.

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

## What generation can and cannot see

Both entry points instantiate the serializer with the same baseline `context`
dispatch renders with — `{"request": None, "format": None, "view": None}` — so a
serializer whose `get_fields` reads `self.context["request"]` is describable, not
just callable. Before, description raised `KeyError` on a serializer the same
spec rendered perfectly.

The view and request are `None` and cannot be otherwise: a schema is built once,
when a transport declares its tools, and there is no request at that moment to
describe. So a `get_fields` that *branches* on the view or the user is reflected
as the branch taken by a caller with neither. Reflection cannot report a field
set that depends on who is asking, because at description time nobody is — if
your field set varies by audience, declare it with
[field markings](audience.md) instead, which are resolved per render.

## How deep generation goes

A nested serializer is walked into, and two things stop the walk.

**A serializer that nests itself is truncated at the re-entry.** A category tree,
a threaded comment, an org chart — the shape is ordinary, and describing it
unbounded is a `RecursionError` raised while a transport *declares its tools*,
before any request exists to fail. So the guard is always on and has nothing to
configure. It follows the current *path*, not everything seen: the same address
serializer under `billing` and under `shipping` sits on two different paths and
is described in full on both.

**`max_depth` is an opt-in ceiling on size.** A schema grows roughly threefold
per nesting level and both agent transports rebuild every tool's schema each
time they list, so a deep declaration is paid for on every listing. It counts
serializer objects — the root is level 1, and the array wrapper `many=True`
produces costs no level:

```python
schema = output_to_json_schema(InvoiceSerializer, kind=SelectorKind.LIST, max_depth=2)
```

Left unset it describes every level, which is what generation has always done.

A truncated node is `{"type": "object"}` — the one thing still known to be true,
and what a caller can still send: an object whose keys the schema declines to
enumerate. **Never `$defs` / `$ref`.** Factoring the repeated sub-schema out is
the obvious answer and it is refused deliberately: most MCP clients reject a tool
schema containing a reference outright, so it would trade a size problem for a
compatibility one. Every schema these helpers emit is flat and self-contained.

## `JsonSchemaRegistry`

::: rest_framework_services.types.json_schema_registry.JsonSchemaRegistry

## `DEFAULT_JSON_SCHEMA_REGISTRY`

::: rest_framework_services.types.json_schema_registry.DEFAULT_JSON_SCHEMA_REGISTRY
