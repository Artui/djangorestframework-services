"""``serializer_to_json_schema`` — input-side JSON Schema for a serializer."""

from __future__ import annotations

import dataclasses
from typing import Any

from rest_framework import serializers

from rest_framework_services.jsonschema.utils import (
    dataclass_to_schema,
    serializer_for_schema,
    serializer_to_schema,
)
from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)


def serializer_to_json_schema(
    serializer: type | None,
    *,
    partial: bool = False,
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
    max_depth: int | None = None,
) -> dict[str, Any]:
    """Build a JSON Schema *object* for an input serializer / dataclass / ``None``.

    Accepts a DRF ``Serializer`` subclass, a bare ``@dataclass`` type (the
    convention drf-services services use for ``data``), or ``None`` (the
    operation takes no input). Always returns an object — ``{"type": "object"}``
    is the convention for "no declared fields", so an alternate transport can
    still describe the tool.

    ``partial=True`` drops the ``required`` list — mirroring ``spec.partial``,
    where the validator accepts omitted fields, so advertising them as required
    would make schema-strict consumers reject calls the service accepts.

    ``registry`` supplies consumer rules for custom field / Python types — see
    [`JsonSchemaRegistry`][rest_framework_services.types.json_schema_registry.JsonSchemaRegistry].

    ``max_depth`` bounds how many serializer levels are described, truncating
    deeper ones to ``{"type": "object"}``; ``None``, the default, describes them
    all, which is what every caller got before the option existed. The root is
    level 1, so ``max_depth=1`` publishes the top-level fields and truncates
    every nested serializer. It is a **size** knob and nothing more: a
    self-referential serializer is truncated at its re-entry whatever this says,
    because the alternative is a ``RecursionError`` raised while a transport
    declares its tools. A dataclass input has no nested-serializer walk, so the
    bound does not reach that branch.

    Truncation is flat and self-contained — never ``$defs`` / ``$ref``, which
    most MCP clients reject outright.
    """
    schema: dict[str, Any]
    if serializer is None:
        schema = {"type": "object"}
    elif isinstance(serializer, type) and issubclass(serializer, serializers.Serializer):
        schema = serializer_to_schema(
            serializer_for_schema(serializer), registry, max_depth=max_depth
        )
    elif isinstance(serializer, type) and dataclasses.is_dataclass(serializer):
        schema = dataclass_to_schema(serializer, registry)
    else:
        schema = {"type": "object"}
    if partial:
        schema.pop("required", None)
    return schema
