"""``annotate_output_schema`` — mirror a projection onto a JSON Schema."""

from __future__ import annotations

from collections.abc import Mapping
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

    Three changes, each the mirror of one the payload undergoes:

    - hidden properties are removed, and dropped from ``required``;
    - a marked field's ``description`` replaces the ``help_text`` one, so a handle
      says what it is for in the schema a model reads without that wording
      leaking into the browsable API;
    - a substituted choice field is re-declared in terms of its **display**
      values, because that is what the projected payload now carries. The
      constant is gone from the response by design — a field another tool takes
      as input should be marked ``HANDLE``, which suppresses the substitution on
      both sides.

    Generating both sides from one declaration is the point: a schema that
    advertises a field the payload no longer carries is worse than either
    behaviour on its own.

    Takes the **item** schema. Callers that wrap items in an envelope of their
    own — an array, or a pagination object — annotate the item and wrap
    afterwards; ``output_to_json_schema(projection=...)`` does exactly that.
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
        resolved = _annotate(subschema, child) if child is not None else subschema
        if audience is not FieldAudience.HANDLE and name in projection.choice_labels:
            resolved = _spoken_schema(resolved, projection.choice_labels[name])
        description = _description(projection, name, audience)
        annotated[name] = {**resolved, "description": description} if description else resolved
    result: dict[str, Any] = {**schema, "properties": annotated}
    required = [name for name in schema.get("required", []) if name in annotated]
    if required:
        result["required"] = required
    else:
        result.pop("required", None)
    return result


def _spoken_schema(schema: dict[str, Any], labels: Mapping[Any, str]) -> dict[str, Any]:
    """Re-declare a choice schema in the display values the payload now carries.

    Both spellings the walker emits are handled: a bare ``enum`` where the labels
    added nothing to some values, and ``oneOf`` / ``const`` / ``title`` where they
    did. ``title`` is dropped with the constant it annotated — repeating the
    value it now equals teaches nothing.

    A ``MultipleChoiceField`` arrives as an array wrapping its member schema, so
    the rewrite descends one level.
    """
    items = schema.get("items")
    if isinstance(items, dict):
        return {**schema, "items": _spoken_schema(items, labels)}
    if "enum" in schema:
        return {**schema, "enum": [labels.get(value, value) for value in schema["enum"]]}
    if "oneOf" in schema:
        return {
            **schema,
            "oneOf": [
                {"const": labels.get(entry["const"], entry["const"])} if "const" in entry else entry
                for entry in schema["oneOf"]
            ],
        }
    return schema


def _description(projection: AgentProjection, name: str, audience: FieldAudience) -> str | None:
    marking = projection.fields.get(name)
    if marking is not None and marking.description:
        return marking.description
    return HANDLE_DESCRIPTION if audience is FieldAudience.HANDLE else None
