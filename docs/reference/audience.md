# Audience projection

One serializer, more than one kind of reader. These helpers let a field say
who it is written for — see the
[recipe](../recipes/agent-audience.md) for the task-shaped version — and let an
agent transport apply that declaration to payloads and schemas from the same
source, so the two cannot disagree.

Nothing here is read by the DRF view path. A serializer marked up for an agent
renders byte-identically behind a viewset.

The value types (`FieldAudience`, `AgentField`, `AgentProjection`, `AGENT`) are
documented under [Types](types.md); the render entry points
(`render_for_agent` / `arender_for_agent`) under
[Dispatch](dispatch.md).

## `build_agent_projection`

::: rest_framework_services.audience.build_agent_projection.build_agent_projection

## `project_payload`

::: rest_framework_services.audience.project_payload.project_payload

## `annotate_output_schema`

::: rest_framework_services.audience.annotate_output_schema.annotate_output_schema

## `HANDLE_DESCRIPTION`

::: rest_framework_services.audience.annotate_output_schema.HANDLE_DESCRIPTION
