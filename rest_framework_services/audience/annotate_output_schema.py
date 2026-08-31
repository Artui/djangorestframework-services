"""``annotate_output_schema`` — mirror a projection onto a JSON Schema."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from rest_framework_services.types.audience_projection import AudienceProjection
from rest_framework_services.types.field_audience import FieldAudience
from rest_framework_services.types.value_formatter import ValueFormatter

_CARRIED: Final = ("title", "description")
"""Keywords that survive a formatter replacing a property's schema.

Both annotate the field rather than asserting anything about its value, so
neither stops being true when the value is rendered differently.
"""


def annotate_output_schema(
    schema: dict[str, Any] | None,
    projection: AudienceProjection,
    *,
    handle_description: str | None = None,
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
    - a formatted field is re-declared as the type its
      [`ValueFormatter`][rest_framework_services.types.value_formatter.ValueFormatter]
      says it produces, plus whatever that declaration adds about the shape of
      the produced value. The framework writes the ``type`` from ``produces``
      rather than taking one from the fragment, so a renderer cannot contradict
      its own advertisement.

    Generating both sides from one declaration is the point: a schema that
    advertises a field the payload no longer carries is worse than either
    behaviour on its own.

    ``handle_description`` is the fallback wording for a ``HANDLE`` that
    declares none of its own, and defaults to **nothing**. Telling a reader what
    to do with an identifier is advice for one kind of reader, and this package
    does not know which kind is reading — a CSV export has no use for it, and
    "do not read this out" only means something to a consumer that reads things
    out. The transport that knows its audience supplies the sentence.

    Takes the **item** schema. Callers that wrap items in an envelope of their
    own — an array, or a pagination object — annotate the item and wrap
    afterwards; ``output_to_json_schema(projection=...)`` does exactly that.
    """
    if schema is None or projection.is_empty():
        return schema
    return _annotate(schema, projection, handle_description)


def _annotate(
    schema: dict[str, Any], projection: AudienceProjection, handle_description: str | None
) -> dict[str, Any]:
    # A list schema wraps the item schema; project the items and keep the array.
    items = schema.get("items")
    if isinstance(items, dict):
        return {**schema, "items": _annotate(items, projection, handle_description)}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema
    annotated: dict[str, Any] = {}
    for name, subschema in properties.items():
        audience = projection.audience(name)
        if audience is FieldAudience.HIDDEN:
            continue
        # The mirror of the chain in ``project_payload``, in the same order and
        # for the same reason: a declared formatter is the transform its author
        # asked for, so it wins over the substitution derived from a
        # ``ChoiceField``. Reorder one of these two and the schema starts
        # describing a payload nobody renders.
        formatter = projection.formatter(name)
        child = projection.nested.get(name)
        if formatter is not None:
            resolved = _formatted_schema(subschema, formatter)
        else:
            resolved = (
                _annotate(subschema, child, handle_description) if child is not None else subschema
            )
            if audience is not FieldAudience.HANDLE and name in projection.choice_labels:
                resolved = _spoken_schema(resolved, projection.choice_labels[name])
        description = _description(projection, name, audience, handle_description)
        annotated[name] = {**resolved, "description": description} if description else resolved
    result: dict[str, Any] = {**schema, "properties": annotated}
    required = [name for name in schema.get("required", []) if name in annotated]
    if required:
        result["required"] = required
    else:
        result.pop("required", None)
    return result


def _formatted_schema(schema: dict[str, Any], formatter: ValueFormatter) -> dict[str, Any]:
    """Re-declare a property as what its formatter produces.

    Every assertion the walk made — ``type``, ``format``, ``enum``, a
    ``MultipleChoiceField``'s ``items`` — described the value the serializer
    rendered, and that value is no longer what the payload carries. A
    ``DateTimeField`` reported ``format: date-time``, which a formatted local
    date-time is not; keeping it would be a schema that fails its own claim
    while looking correct.

    ``_CARRIED`` is what survives, because it annotates the *field* rather than
    asserting anything about its value: an author's ``label`` and their
    ``help_text``. The formatter's own fragment merges over both, so a
    formatter that wants to say something else about the field still can.
    """
    carried = {key: schema[key] for key in _CARRIED if key in schema}
    return {**carried, **formatter.json_schema()}


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


def _description(
    projection: AudienceProjection,
    name: str,
    audience: FieldAudience,
    handle_description: str | None,
) -> str | None:
    marking = projection.fields.get(name)
    if marking is not None and marking.description:
        return marking.description
    return handle_description if audience is FieldAudience.HANDLE else None
