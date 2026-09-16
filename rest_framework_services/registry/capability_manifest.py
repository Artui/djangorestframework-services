"""``capability_manifest`` — the operations a registry exposes, as one document."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from rest_framework_services.dispatch.unguarded_specs import unguarded_specs
from rest_framework_services.jsonschema.spec_to_json_schema import spec_to_json_schema
from rest_framework_services.registry.spec_registry import SpecRegistry
from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)
from rest_framework_services.types.registered_spec import RegisteredSpec
from rest_framework_services.types.service_spec import ServiceSpec

_MANIFEST_VERSION: Final = 1
"""The document's own format version, bumped when a key changes meaning or moves.

Adding a key does not bump it; a reader ignores keys it does not know.
"""

_SCHEMA_DIALECT: Final = "rest_framework_services.jsonschema/1"
"""Names the schema policy every ``*_schema`` value in the document follows.

Not a JSON Schema meta-schema URI, because that would not distinguish anything:
the vocabulary is plain JSON Schema, and a ``$defs`` / ``$ref`` document is
valid under the same meta-schema. What differs between emitters is presentation
-- this package inlines every nested shape, truncates a self-nesting serializer
after a fixed number of appearances rather than referencing it, spells a decimal
as a formatted string, and adds no titles it was not given. Two manifests are
comparable schema-for-schema only when this value matches, so it moves when that
policy does.
"""


def capability_manifest(
    registry: SpecRegistry,
    *,
    schema_registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
) -> dict[str, Any]:
    """Describe every operation in ``registry``: what exists and what shape it takes.

    The surface a transport already derives piecemeal -- an MCP tool list, an agent
    toolset's descriptions, a frontend's action menu -- as one JSON-native
    document, derived from the registry rather than written by hand:

    ```python
    {
        "version": 1,
        "dialect": "rest_framework_services.jsonschema/1",
        "operations": [
            {
                "name": "refund_order",
                "kind": "mutation",
                "tags": ["admin", "write"],
                "idempotent": True,
                "input_schema": {"type": "object", "properties": {...}},
                "output_schema": {"type": "object", "properties": {...}},
                "guards": [{"source": "orders.permissions.IsSupport"}],
            },
            ...
        ],
        "unguarded": ["list_orders"],
    }
    ```

    **It names no principal and decides nothing**, deliberately. It is the same
    for every caller, so it says which guards an operation carries and never what
    they grant: a permission class is code, and nothing short of running it
    against a principal answers that. A reader that understands a declarative
    policy can layer a decision onto each operation; this document is what such a
    reader starts from. For the same reason it is **not a document to serve to a
    client** -- it enumerates operations a given caller may be unable to see at
    all.

    Keys, per operation:

    - ``name`` -- the registry name, which is the operation's identity across
      transports.
    - ``kind`` -- ``"mutation"`` for a ``ServiceSpec``, ``"query"`` for a
      ``SelectorSpec``, derived by type exactly as the registry derives it.
    - ``tags`` -- sorted, so two manifests of one registry are byte-identical.
    - ``idempotent`` -- ``ServiceSpec.idempotent`` as declared, ``None`` included:
      silence is not a claim, and a reader must be able to tell it from a declared
      ``False``. Always ``None`` on a query, which has no such declaration -- its
      ``kind`` already says it changes nothing.
    - ``input_schema`` / ``output_schema`` -- exactly what
      [`spec_to_json_schema`][rest_framework_services.jsonschema.spec_to_json_schema.spec_to_json_schema]
      derives for each phase, so the document carries this package's schema
      dialect rather than a second one; ``output_schema`` is ``None`` where no
      output is declared. ``dialect`` names that policy at the top level.
    - ``guards`` -- one ``{"source": <dotted class path>}`` per permission class,
      in declaration order; ``None`` where the spec declares none (``[]`` is a
      declared "no permissions" and stays distinguishable).

    ``unguarded`` is ``unguarded_specs`` over the same registry: the operations
    whose ``permission_classes`` is ``None``, which have nothing to inherit off
    HTTP.

    Pure: it reads declarations and never the database, so building it at
    startup, in a management command, or in a test costs the schema walk and
    nothing else.

    Args:
        registry: The operations to describe, in registration order.
        schema_registry: Consumer rules for custom field / filter / Python types,
            forwarded to the schema derivation -- the same object a transport
            passes to ``spec_to_json_schema``, so the manifest and the transport
            describe a custom field identically.

    Raises:
        TypeError: ``registry`` is not a ``SpecRegistry``. A bare ``name -> spec``
            mapping has no tags, so every operation would be reported untagged --
            a manifest that is quietly smaller than the one a registry produces.
    """
    if not isinstance(registry, SpecRegistry):
        raise TypeError(
            "capability_manifest expects a SpecRegistry; got "
            f"{type(registry).__name__}. A bare name -> spec mapping carries no tags, so "
            "the manifest would report every operation untagged -- register the specs "
            "instead."
        )
    return {
        "version": _MANIFEST_VERSION,
        "dialect": _SCHEMA_DIALECT,
        "operations": [_operation(entry, schema_registry) for entry in registry],
        "unguarded": unguarded_specs(registry.specs()),
    }


def _operation(entry: RegisteredSpec, schema_registry: JsonSchemaRegistry) -> dict[str, Any]:
    spec = entry.spec
    kind, idempotent = (
        ("mutation", spec.idempotent) if isinstance(spec, ServiceSpec) else ("query", None)
    )
    return {
        "name": entry.name,
        "kind": kind,
        "tags": sorted(entry.tags),
        "idempotent": idempotent,
        "input_schema": spec_to_json_schema(spec, phase="input", registry=schema_registry),
        "output_schema": spec_to_json_schema(spec, phase="output", registry=schema_registry),
        "guards": _guards(spec.permission_classes),
    }


def _guards(permission_classes: Sequence[type] | None) -> list[dict[str, str]] | None:
    """One descriptor per permission class, named by its import path.

    The dotted path rather than the bare class name, because two apps routinely
    declare an ``IsOwner`` each and a reader diffing manifests must be able to tell
    which one moved.
    """
    if permission_classes is None:
        return None
    return [{"source": f"{cls.__module__}.{cls.__qualname__}"} for cls in permission_classes]


__all__ = ["capability_manifest"]
