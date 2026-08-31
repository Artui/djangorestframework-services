# Mark fields for an agent audience

One serializer often has more than one kind of reader. A frontend needs the
primary key to route on and ignores the rest of the plumbing. A model exposed to
the same spec as a tool has no way to tell which fields are for it and which are
for the person it is talking to — so it reads records out by UUID, says
`PENDING_REVIEW` where a person would say "Awaiting review", and narrates an
ETag as if it were content.

That is not a prompting problem on the client. The agent mirrors the shape it
was handed. Declare the difference on the server, once, on the field.

## Declare it

The marking is an
[`FieldMarking`][rest_framework_services.types.field_marking.FieldMarking] in DRF's
per-field `style` bag, under the
[`MARKING`][rest_framework_services.types.field_marking.MARKING] key:

```python
from rest_framework import serializers
from rest_framework_services import MARKING, FieldMarking


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["id", "number", "status", "customer", "etag", "amount"]
        extra_kwargs = {
            "id": {"style": {MARKING: FieldMarking.handle("Invoice handle for other tools.")}},
            "customer": {"style": {MARKING: FieldMarking.handle()}},
            "etag": {"style": {MARKING: FieldMarking.hidden()}},
            "number": {"style": {MARKING: FieldMarking.label()}},
        }
```

Four audiences, and the default is "content":

| Marking | Meaning | Agent payload | Agent schema | REST |
| --- | --- | --- | --- | --- |
| *(unmarked)* | content | included | included | unchanged |
| `FieldMarking.label()` | names this record for a human | included | its `description`, if given | unchanged |
| `FieldMarking.handle()` | opaque; passed to tools, never spoken | verbatim | its `description`, if given | unchanged |
| `FieldMarking.hidden()` | plumbing | **dropped** | dropped | unchanged |
| `FieldMarking.timestamp()` | content, formatted | rendered through the formatter | the type the formatter produces | unchanged |

`style` is used because it is the only door `Meta.extra_kwargs` opens onto a
field constructor — anything else would mean subclassing every field type and
losing `ModelSerializer`'s auto-generation, which is the whole point of keeping
one serializer. DRF consults `style` only in `HTMLFormRenderer`, and only for
its own keys, so **your REST responses do not change**.

## Apply it

An agent transport renders with
[`render_for_audience`][rest_framework_services.dispatch.render_for_audience.render_for_audience]
instead of `render_spec_output`:

```python
payload = render_for_audience(spec, result.value, many=True)
```

`etag` is gone, `status` reads `"Awaiting review"`, and `id` is untouched — a
handle is somebody else's input, so its constant is never re-spelled.

For the schema side, hand the same projection to
[`output_to_json_schema`][rest_framework_services.jsonschema.output_to_json_schema.output_to_json_schema]
so both sides come from the one declaration:

```python
projection = build_audience_projection(InvoiceSerializer)

schema = output_to_json_schema(InvoiceSerializer, kind=SelectorKind.LIST, projection=projection)
payload = render_for_audience(spec, value, many=True, projection=projection)
```

Pass the same `kind` / `paginate` you dispatch with, or the schema describes the
wrong cardinality. The projection lands on the **item** wherever the item sits:
inside the array for a list, inside `items` for a paginated envelope. Those
wrappers are the generator's own shapes and belong to no serializer.

**That call is the main path for the output phase, not a detour around one.**
`spec_to_json_schema` is the convenience for the *input* phase, and that is the
only phase either shipped transport uses it for. For output, both the MCP server
and the Pydantic-AI toolset read the spec's output serializer and `kind`
themselves and call `output_to_json_schema` directly with the full argument set
— `paginate`, `projection` and `handle_description` — because those three are
the transport's own answers and no spec carries them. Writing the call out the
way this recipe does puts you on the road they are already on.

Building the projection instantiates the serializer, so a transport that
registers its tools up front should build it **once at registration** and pass
it to every render, as above.

A substituted choice field is re-declared in the schema in its *display* values,
because that is what the payload now carries. If another tool takes that field
as input, mark it `FieldMarking.handle()` — that suppresses the substitution on
both sides and keeps the constant.

## Format a value for the agent

An agent reading `2026-01-31T13:00:00Z` out to a person is doing the same thing
it does with `PENDING_REVIEW`: repeating a machine's spelling because nothing
told it there was another one. A
[`ValueFormatter`][rest_framework_services.types.value_formatter.ValueFormatter]
is that declaration, and `FieldMarking.timestamp()` is the one you probably want:

```python
extra_kwargs = {
    "due_at": {"style": {MARKING: FieldMarking.timestamp()}},
    "closed_at": {"style": {MARKING: FieldMarking.timestamp("%d %b %Y")}},
}
```

The payload now carries `"31 Jan 2026 14:05"`, and the schema says
`{"type": "string", "examples": ["31 Jan 2026 14:05"]}` — the example is
rendered from the same format string, so it cannot describe a shape the field
does not produce.

### The zone is Django's, and it is not an argument

`timestamp()` takes a format, never a timezone. DRF's `DateTimeField` already
renders in `django.utils.timezone.get_current_timezone()`, so reading the same
source is what makes the REST response and the agent payload agree by
construction rather than by discipline — activate the zone the way you already
do for the ORM, in per-tenant middleware or at the top of a worker, and both
transports follow.

A *callable* zone is not merely unsupported. The projection is built once per
serializer class, and the schema half is built with no request at all, because
a transport describes its tools before anybody has called them. A per-request
callable would therefore resolve differently on the two paths, which is the
schema-versus-payload divergence this whole feature exists to prevent. The
schema describes a formatted string and never names a zone, which is what keeps
it honest.

### Any other transform

`timestamp()` is a named constructor over a generic mechanism, not a special
case inside one. Money with its currency, a duration, a percentage, a quantity
with its unit — all of them are the same shape:

```python
from rest_framework_services import FieldMarking, ValueFormatter

total = FieldMarking.formatted(
    ValueFormatter(
        lambda cents: f"EUR {cents / 100:.2f}",
        produces="string",
        schema={"examples": ["EUR 1240.00"]},
    )
)
```

**`produces=` names the JSON type and the framework writes it into the schema.**
`schema=` merges over that for anything about the *shape* of the produced value
— `description`, `examples`, `format` — and is refused if it names `type`.
Choice substitution cannot lie because both sides are derived from the same
`ChoiceField`; a formatter you supply could, so the declaration carries what it
produces and the schema is written from that rather than taken on trust.

Everything the schema previously asserted about the field is dropped, because it
described a value the payload no longer carries — a formatted local date-time is
not `format: date-time`. An author's `label` and `help_text` survive: they
annotate the field rather than asserting anything about its value. A `None` never
reaches the formatter.

### Two collisions, decided rather than left to chance

- **An explicit formatter beats the substitution derived from a `ChoiceField`.**
  Only one transform can apply, and the one written by hand is the one that was
  asked for.
- **`HANDLE` suppresses formatting**, exactly as it suppresses choice
  substitution. A formatted machine identifier is a broken one. Declaring both
  on one field is honoured as `HANDLE` and the formatter never runs.

⇒ **a field a second tool takes as input wants `FieldMarking.handle()`.**
Otherwise that tool is handed `"31 Jan 2026 14:05"` for an argument its own
input schema declares as a date-time, and rejects.

## One mount that needs what its sibling hides

The serializer is the declaration and stays authoritative. The exception is a
single tool that must return a field its neighbours drop — a lookup whose whole
job is handing back the identifier the list view hides. That is an override, and
it belongs on the registry entry rather than on any one transport:

```python
registry.register(
    "lookup_invoice",
    lookup_spec,
    agent_contract=OfflineContract(field_audiences={"etag": FieldMarking()}),
)
```

Every agent transport reads that one contract, so the field set an agent sees
cannot depend on which transport served it. `build_audience_projection` layers it:

```python
projection = build_audience_projection(
    InvoiceSerializer, overrides=contract.field_audiences, name="lookup_invoice"
)
```

An override can move the label and it can introduce the clash the serializer
could not have — two fields marked `FieldMarking.label()` from two places — which
raises `ImproperlyConfigured` naming the mount.

**A project that wants two agent audiences with different visibility does not
want this.** It wants two serializers, or a real second audience.
`FieldAudience`'s axis is audience, not transport, and reaching for per-mount
overrides to get a second one produces something the serializer's own markings
then contradict.

## Nesting

The marking lives on the field object, not in a list on `Meta`. A nested
serializer's fields carry their own audience wherever that serializer appears,
so there is no hoisting rule to learn and nothing to keep in sync when a field
is renamed:

```python
class LineSerializer(serializers.Serializer):
    sku = serializers.CharField(style={MARKING: FieldMarking.handle()})
    description = serializers.CharField()


class InvoiceSerializer(serializers.Serializer):
    number = serializers.CharField(style={MARKING: FieldMarking.label()})
    lines = LineSerializer(many=True)
```

## Two mistakes that raise rather than pass quietly

- A value under `MARKING` that is not an `FieldMarking` — the shape a half-finished
  migration leaves behind (`{"style": {MARKING: "handle"}}`). It would otherwise
  do nothing at all.
- Two fields both marked `FieldMarking.label()`. A record has one name; silently
  picking the first is the kind of thing you find in a transcript weeks later.

Both raise `ImproperlyConfigured` naming the serializer and the field.

## What this deliberately does not do

It does not relocate internal fields into a reserved subtree. A payload that a
transport also emits as text is read in full either way, so moving a field costs
its keys a second time and hides nothing. What an agent must never use is
dropped; what it must pass on but never speak stays where it is and says so in
the schema.
