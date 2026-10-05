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

_SCALAR_TYPES: Final = ((bool, "boolean"), (int, "integer"), (float, "number"), (str, "string"))
"""JSON's name for each kind of value a choice can hold, ``bool`` before the
``int`` it subclasses. ``None`` is handled apart: whether a restated type admits
``"null"`` is read off the type that was stated, and it is named last."""


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
      values, because that is what the projected payload now carries. A
      ``type`` stated beside them is restated as the types of the displays,
      so an integer choice spoken as ``"Low"`` is described as a ``string``,
      with ``"null"`` kept where the stated type admitted it, and left as
      written beside a ``oneOf`` entry that admits more than its constants. A
      display two values share is listed once, so a row served it matches one
      ``oneOf`` entry rather than two, and an array of such choices no longer
      claims ``uniqueItems``. The constant is gone from the response by design
      — a field another tool takes as input should be marked ``HANDLE``, which
      suppresses the substitution on both sides.
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

    Django allows two values one display, and a display is listed once, where
    first seen: ``oneOf`` admits a value valid under exactly one entry, so a
    display listed twice would refuse every row served it. A ``oneOf`` entry
    with no ``const`` is kept wherever it stands. The type stated beside the
    values is restated by ``_retyped``.

    A ``MultipleChoiceField`` arrives as an array wrapping its member schema, so
    the rewrite descends one level. Its ``uniqueItems`` held for the stored
    values and not for their displays: where the member's values collapse
    onto fewer displays, two values selected together are served as one
    display twice, and the payload keeps both because both are selected, so
    the array stops claiming its items are unique. A union arrives as
    ``anyOf`` — the shape an ``X | None`` annotation is described in, which is
    how a dataclass output's optional ``Enum`` field reaches here — and each
    member is rewritten, the null one passing through untouched. The
    union's own ``type``, where one is stated, is left as written.
    """
    items = schema.get("items")
    if isinstance(items, dict):
        spoken_items = _spoken_schema(items, labels)
        spoken = {**schema, "items": spoken_items}
        if _listed_count(spoken_items) < _listed_count(items):
            # Held by TestSharedDisplays.test_an_array_of_shared_displays_stops_claiming_unique_items
            # (the drop) and test_an_array_of_distinct_displays_keeps_unique_items
            # (the condition), end to end by
            # test_two_selected_values_sharing_a_display_validate_against_their_schema.
            spoken.pop("uniqueItems", None)
        return spoken
    if "anyOf" in schema:
        return {**schema, "anyOf": [_spoken_schema(member, labels) for member in schema["anyOf"]]}
    if "enum" in schema:
        values: list[Any] = []
        for value in schema["enum"]:
            display = labels.get(value, value)
            if not _is_listed(display, values):
                values.append(display)
        return _retyped({**schema, "enum": values}, values)
    if "oneOf" in schema:
        one_of: list[Any] = []
        consts: list[Any] = []
        for entry in schema["oneOf"]:
            if "const" not in entry:
                one_of.append(entry)
                continue
            display = labels.get(entry["const"], entry["const"])
            if not _is_listed(display, consts):
                consts.append(display)
                one_of.append({"const": display})
        spoken = {**schema, "oneOf": one_of}
        # An entry with no ``const`` admits values the displays say nothing
        # about, so a type narrowed to the displays' would refuse them; one
        # whose type is ``"null"`` admits only the null ``_retyped`` keeps.
        # One branch to coverage, so each condition is held by its own test:
        # TestRestatedType.test_an_integer_spoken_whole_is_a_string (the first:
        # without it every entry keeps the type) and
        # TestRestatedType.test_null_is_kept_where_the_stated_type_admitted_it
        # (the second).
        if any("const" not in entry and entry.get("type") != "null" for entry in one_of):
            return spoken
        return _retyped(spoken, consts)
    return schema


def _listed_count(schema: dict[str, Any]) -> int:
    """How many values a choice schema lists, across a union's members."""
    members: list[Any] = schema.get("anyOf", [schema])
    return sum(len(member.get("enum", ())) + len(member.get("oneOf", ())) for member in members)


def _is_listed(value: Any, listed: list[Any]) -> bool:
    """Whether ``value`` is already in ``listed``, as JSON Schema compares values.

    ``True == 1`` in Python and not in JSON Schema, where a boolean is never a
    number, so dropping one as a repeat of the other would refuse every row
    served it.
    """
    # One branch to coverage, so each condition is held by its own test:
    # TestSharedDisplays.test_an_enum_lists_each_display_once_in_first_seen_order
    # (the first: without it every value after the first is a repeat) and
    # TestSharedDisplays.test_a_boolean_is_not_the_number_python_says_it_equals
    # (the second).
    return any(
        value == seen and isinstance(value, bool) is isinstance(seen, bool) for seen in listed
    )


def _retyped(schema: dict[str, Any], values: list[Any]) -> dict[str, Any]:
    """``schema`` with any stated ``type`` restated as the types of ``values``.

    The walk states a choice's type beside its values, and a display is a
    string whatever the value it replaced was, so an integer choice spoken as
    ``"Low"`` would otherwise be described as an integer and the projected
    payload would fail the projected schema.

    ``"null"`` is kept exactly where the stated type admitted it, as a list
    naming it or as ``"null"`` alone, and named last. It is read off the type
    rather than the values because the type is what admitted or refused a null
    before, and a ``oneOf`` may admit one through an entry whose type is
    ``"null"``: the restated type admits what the stated one did, in display
    terms, and nothing more. A ``oneOf`` entry admitting anything else is never
    passed here, because narrowing the type would refuse what that entry admits.

    A schema that states no type claims nothing to contradict, one listing no
    value has nothing to restate it from, and a value JSON has no scalar name
    for — or nothing but a null the type refused — leaves the type as written
    rather than guessed at.
    """
    # One branch to coverage, so each condition is held by its own test:
    # TestRestatedType.test_an_untyped_choice_states_no_type (the first) and
    # TestRestatedType.test_a_choice_listing_nothing_keeps_its_type (the second).
    if "type" not in schema or not values:
        return schema
    names: list[str] = []
    for value in values:
        if value is None:
            continue
        name = next(
            (json_name for kind, json_name in _SCALAR_TYPES if isinstance(value, kind)), None
        )
        if name is None:
            return schema
        if name not in names:
            names.append(name)
    stated = schema["type"]
    # Both spellings of a stated null: TestRestatedType.test_a_nullable_integer_keeps_its_null
    # (a list naming it) and TestRestatedType.test_a_type_stated_as_null_alone_still_admits_it
    # (``"null"`` alone).
    if "null" in (stated if isinstance(stated, list) else [stated]):
        names.append("null")
    if not names:
        return schema
    return {**schema, "type": names[0] if len(names) == 1 else names}


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
