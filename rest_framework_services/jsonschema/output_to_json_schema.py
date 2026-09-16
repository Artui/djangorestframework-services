"""``output_to_json_schema`` — output-side JSON Schema, LIST/pagination aware."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from rest_framework import serializers

from rest_framework_services.audience.annotate_output_schema import annotate_output_schema
from rest_framework_services.audience.utils import AFFORDANCES_KEY, affordance_schema
from rest_framework_services.jsonschema.utils import (
    dataclass_to_schema,
    serializer_for_schema,
    serializer_to_schema,
)
from rest_framework_services.types.audience_projection import AudienceProjection
from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.service_spec import ServiceSpec


def output_to_json_schema(
    output_serializer: type | None,
    *,
    kind: SelectorKind | None = None,
    paginate: bool = False,
    projection: AudienceProjection | None = None,
    handle_description: str | None = None,
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
    max_depth: int | None = None,
    affordances: Mapping[str, ServiceSpec[Any, Any, Any]] | None = None,
) -> dict[str, Any] | None:
    """Build a JSON Schema for an output serializer, or ``None`` when undeclared.

    Returns ``None`` when there is no ``output_serializer`` — callers shouldn't
    fabricate a misleading shape — and when the output is a ``BaseSerializer``
    subclass that is not a ``Serializer``, which renders but declares no fields.
    Anything that is neither a ``BaseSerializer`` subclass nor a dataclass type
    raises ``TypeError``: ``None`` would read as "no output declared" for a
    declaration whose output cannot be rendered at all. A transport building
    schemas lazily, per listing request, meets that error there, so build them
    where the spec is registered. ``kind`` / ``paginate`` make the schema match
    what dispatch actually returns:

    - ``kind=None`` / ``RETRIEVE`` — the bare item schema.
    - ``kind=LIST, paginate=False`` — ``{type: array, items: <item>}``.
    - ``kind=LIST, paginate=True`` — the pagination envelope
      ``{items, page, totalPages, hasNext}``.

    ``projection`` applies the serializer's field markings, mirroring what
    [`project_payload`][rest_framework_services.audience.project_payload.project_payload]
    does to the payload — hidden fields dropped, choices re-declared in their
    display values, and a formatted field re-declared as the type its
    [`ValueFormatter`][rest_framework_services.types.value_formatter.ValueFormatter]
    produces. It lands on the **item**, wherever the item sits for this
    ``kind`` — the array wrapper and the pagination envelope are this function's
    own shapes and belong to no serializer, so a projection walking them would
    look for markings that cannot exist and silently annotate nothing.

    ``handle_description`` is passed through to
    [`annotate_output_schema`][rest_framework_services.audience.annotate_output_schema.annotate_output_schema]
    as the fallback wording for an unlabelled handle. It defaults to nothing:
    what a reader should *do* with an identifier depends on the reader, and the
    transport is what knows.

    ``registry`` supplies consumer rules for custom field / Python types — see
    [`JsonSchemaRegistry`][rest_framework_services.types.json_schema_registry.JsonSchemaRegistry].

    ``max_depth`` bounds how many serializer levels the **item** describes,
    truncating deeper ones to ``{"type": "object"}``; ``None``, the default,
    describes them all. The item is level 1, and the array wrapper and the
    pagination envelope are this function's own shapes, so they cost no level.
    Independently of this bound, a serializer that nests itself is truncated
    after a fixed number of appearances rather than recursing until the process
    dies; where the two disagree the tighter wins, so this still yields exactly
    the levels it names. Truncation is flat and self-contained — never ``$defs``
    / ``$ref``, which most MCP clients reject outright.

    ``affordances`` is the rendering selector spec's ``affordances`` mapping, and
    declares the ``affordances`` object every rendered item then carries: per
    name, a required ``available`` boolean, plus the ``code`` -- enumerated from
    the declaration -- and ``reason`` a refused answer adds, with or without a
    ``projection``, since the agent audience reads the same answers a browser
    does. Pass it wherever the payload is
    rendered by ``render_spec_output`` or ``render_for_audience`` from a spec that
    declares them;
    [`spec_to_json_schema`][rest_framework_services.jsonschema.spec_to_json_schema.spec_to_json_schema]
    does so itself.
    """
    item_schema: dict[str, Any] | None = _item_schema(output_serializer, registry, max_depth)
    if item_schema is None:
        return None
    if projection is not None:
        item_schema = (
            annotate_output_schema(item_schema, projection, handle_description=handle_description)
            or item_schema
        )
    if affordances is not None:
        # After the projection, which walks serializer markings and has nothing to
        # say about a key no serializer declares.
        item_schema = {
            **item_schema,
            "properties": {
                **item_schema.get("properties", {}),
                AFFORDANCES_KEY: affordance_schema(affordances),
            },
            "required": [*item_schema.get("required", []), AFFORDANCES_KEY],
        }
    if kind is not SelectorKind.LIST:
        return item_schema
    array_schema: dict[str, Any] = {"type": "array", "items": item_schema}
    if not paginate:
        return array_schema
    return {
        "type": "object",
        "properties": {
            "items": array_schema,
            "page": {"type": "integer"},
            "totalPages": {"type": "integer"},
            "hasNext": {"type": "boolean"},
        },
        "required": ["items", "page", "totalPages", "hasNext"],
    }


def _item_schema(
    output_serializer: type | None, registry: JsonSchemaRegistry, max_depth: int | None
) -> dict[str, Any] | None:
    if output_serializer is None:
        return None
    if isinstance(output_serializer, type) and issubclass(
        output_serializer, serializers.Serializer
    ):
        return serializer_to_schema(
            serializer_for_schema(output_serializer), registry, for_output=True, max_depth=max_depth
        )
    if isinstance(output_serializer, type) and dataclasses.is_dataclass(output_serializer):
        return dataclass_to_schema(output_serializer, registry)
    if isinstance(output_serializer, type) and issubclass(
        output_serializer, serializers.BaseSerializer
    ):
        # DRF's read-only pattern: it renders through ``render_spec_output`` like
        # any serializer and declares no fields, so there is nothing to describe
        # and ``None`` is the honest answer. Narrower than the input side's rule
        # on purpose -- an input has to validate, and a ``BaseSerializer`` with
        # only ``to_representation`` cannot.
        return None
    # Anything else used to fall through to ``None`` too -- the answer for a spec
    # declaring no output at all -- while ``render_spec_output`` raised the first
    # time it instantiated the declaration. Refused here instead, at declaration
    # time rather than mid-call.
    raise TypeError(
        f"Cannot derive an output JSON Schema from {output_serializer!r}: an output "
        f"serializer must be a BaseSerializer subclass or a dataclass type. Declared "
        f"as it is, rendering the spec's output would fail at the first call."
    )
