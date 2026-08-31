# Audience projection

One serializer, more than one kind of reader. These helpers let a field say
who it is written for — see the
[recipe](../recipes/agent-audience.md) for the task-shaped version — and let an
agent transport apply that declaration to payloads and schemas from the same
source, so the two cannot disagree.

Nothing here is read by the DRF view path. A serializer marked up for an agent
renders byte-identically behind a viewset.

Nor is there any wording for a model here. The markings say what a field *is*;
what a reader should do about it depends on the reader, and the transport is
what knows — so `annotate_output_schema` takes the sentence rather than
supplying one.

The value types (`FieldAudience`, `FieldMarking`, `AudienceProjection`, `MARKING`) are
documented under [Types](types.md); the render entry points
(`render_for_audience` / `arender_for_audience`) under
[Dispatch](dispatch.md).

## `audience_projection_for_spec`

::: rest_framework_services.audience.audience_projection_for_spec.audience_projection_for_spec

## `build_audience_projection`

::: rest_framework_services.audience.build_audience_projection.build_audience_projection

## `project_payload`

::: rest_framework_services.audience.project_payload.project_payload

## `annotate_output_schema`

::: rest_framework_services.audience.annotate_output_schema.annotate_output_schema
