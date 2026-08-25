"""``annotate_output_schema`` — mirror a projection onto a JSON Schema."""

from __future__ import annotations

from typing import Any

from rest_framework_services.types.agent_projection import AgentProjection
from rest_framework_services.types.field_audience import FieldAudience

HANDLE_DESCRIPTION = (
    "Opaque identifier. Pass it to other tools that ask for one; do not read it out."
)
"""Fallback wording for a ``HANDLE`` that declares no ``description`` of its own."""


def annotate_output_schema(
    schema: dict[str, Any] | None, projection: AgentProjection
) -> dict[str, Any] | None:
    """Apply the same projection to a schema that
    [`project_payload`][rest_framework_services.audience.project_payload.project_payload]
    applies to the payload.

    Hidden properties are removed (and dropped from ``required``), and a marked
    field's ``description`` replaces the ``help_text`` one — a handle can then
    say what it is for in the schema a model actually reads, without that wording
    leaking into the browsable API.

    Generating both sides from one declaration is the point: a schema that
    advertises a field the payload no longer carries is worse than either
    behaviour on its own.
    """
    if schema is None or projection.is_empty():
        return schema
    return _annotate(schema, projection)


def _annotate(schema: dict[str, Any], projection: AgentProjection) -> dict[str, Any]:
    # A list schema wraps the item schema; project the items and keep the array.
    items = schema.get("items")
    if isinstance(items, dict):
        return {**schema, "items": _annotate(items, projection)}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema
    annotated: dict[str, Any] = {}
    for name, subschema in properties.items():
        audience = projection.audience(name)
        if audience is FieldAudience.HIDDEN:
            continue
        child = projection.nested.get(name)
        resolved = (
            _annotate(subschema, child)
            if child is not None and isinstance(subschema, dict)
            else subschema
        )
        description = _description(projection, name, audience)
        annotated[name] = {**resolved, "description": description} if description else resolved
    result: dict[str, Any] = {**schema, "properties": annotated}
    required = [name for name in schema.get("required", []) if name in annotated]
    if required:
        result["required"] = required
    else:
        result.pop("required", None)
    return result


def _description(projection: AgentProjection, name: str, audience: FieldAudience) -> str | None:
    marking = projection.fields.get(name)
    if marking is not None and marking.description:
        return marking.description
    return HANDLE_DESCRIPTION if audience is FieldAudience.HANDLE else None
