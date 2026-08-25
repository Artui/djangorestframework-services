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
[`AgentField`][rest_framework_services.types.agent_field.AgentField] in DRF's
per-field `style` bag, under the
[`AGENT`][rest_framework_services.types.agent_field.AGENT] key:

```python
from rest_framework import serializers
from rest_framework_services import AGENT, AgentField


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["id", "number", "status", "customer", "etag", "amount"]
        extra_kwargs = {
            "id": {"style": {AGENT: AgentField.handle("Invoice handle for other tools.")}},
            "customer": {"style": {AGENT: AgentField.handle()}},
            "etag": {"style": {AGENT: AgentField.hidden()}},
            "number": {"style": {AGENT: AgentField.label()}},
        }
```

Four audiences, and the default is "content":

| Marking | Meaning | Agent payload | Agent schema | REST |
| --- | --- | --- | --- | --- |
| *(unmarked)* | content | included | included | unchanged |
| `AgentField.label()` | names this record for a human | included | — | unchanged |
| `AgentField.handle()` | opaque; passed to tools, never spoken | verbatim | `description` says so | unchanged |
| `AgentField.hidden()` | plumbing | **dropped** | dropped | unchanged |

`style` is used because it is the only door `Meta.extra_kwargs` opens onto a
field constructor — anything else would mean subclassing every field type and
losing `ModelSerializer`'s auto-generation, which is the whole point of keeping
one serializer. DRF consults `style` only in `HTMLFormRenderer`, and only for
its own keys, so **your REST responses do not change**.

## Apply it

An agent transport renders with
[`render_for_agent`][rest_framework_services.dispatch.render_for_agent.render_for_agent]
instead of `render_spec_output`:

```python
payload = render_for_agent(spec, result.value, many=True)
```

`etag` is gone, `status` reads `"Awaiting review"`, and `id` is untouched — a
handle is somebody else's input, so its constant is never re-spelled.

For the schema side, pair
[`build_agent_projection`][rest_framework_services.audience.build_agent_projection.build_agent_projection]
with
[`annotate_output_schema`][rest_framework_services.audience.annotate_output_schema.annotate_output_schema]
so both sides come from the one declaration and cannot disagree:

```python
projection = build_agent_projection(spec.output_serializer)
schema = annotate_output_schema(output_to_json_schema(spec.output_serializer), projection)
payload = render_for_agent(spec, value, many=True, projection=projection)
```

Building the projection instantiates the serializer, so a transport that
registers its tools up front should build it **once at registration** and pass
it to every render, as above.

## Nesting

The marking lives on the field object, not in a list on `Meta`. A nested
serializer's fields carry their own audience wherever that serializer appears,
so there is no hoisting rule to learn and nothing to keep in sync when a field
is renamed:

```python
class LineSerializer(serializers.Serializer):
    sku = serializers.CharField(style={AGENT: AgentField.handle()})
    description = serializers.CharField()


class InvoiceSerializer(serializers.Serializer):
    number = serializers.CharField(style={AGENT: AgentField.label()})
    lines = LineSerializer(many=True)
```

## Two mistakes that raise rather than pass quietly

- A value under `AGENT` that is not an `AgentField` — the shape a half-finished
  migration leaves behind (`{"style": {AGENT: "handle"}}`). It would otherwise
  do nothing at all.
- Two fields both marked `AgentField.label()`. A record has one name; silently
  picking the first is the kind of thing you find in a transcript weeks later.

Both raise `ImproperlyConfigured` naming the serializer and the field.

## What this deliberately does not do

It does not relocate internal fields into a reserved subtree. A payload that a
transport also emits as text is read in full either way, so moving a field costs
its keys a second time and hides nothing. What an agent must never use is
dropped; what it must pass on but never speak stays where it is and says so in
the schema.
