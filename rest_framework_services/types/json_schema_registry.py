"""``JsonSchemaRegistry`` — consumer-extensible type → JSON Schema mappings."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

# A single mapping rule: ``(type, schema_fragment)``.
_Rule = tuple[type, dict[str, Any]]


# The built-in mappings live with the walkers, not here (the filter built-ins
# import django-filter lazily): a registry carries only consumer additions, so
# the empty default stays dependency-free.
@dataclass(frozen=True)
class JsonSchemaRegistry:
    """Consumer-extensible ``type → JSON Schema fragment`` rules for the helpers.

    Rules are tried in order and the first match wins, **before** the built-in
    mappings — so a rule both adds support for a custom type and can override a
    built-in. The matched fragment is copied per use, so callers may freely
    mutate the returned schema.

    Immutable: ``extend`` returns a *new* registry rather than mutating, so
    there is no shared global state to leak across callers or tests. Start from
    [`DEFAULT_JSON_SCHEMA_REGISTRY`][rest_framework_services.types.json_schema_registry.DEFAULT_JSON_SCHEMA_REGISTRY], the empty base every
    ``*_to_json_schema`` helper falls back to, layer rules on, and pass the
    result via the helpers' ``registry=`` argument:

        registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
            fields=[(MoneyField, {"type": "string", "format": "money"})],
        )
        schema = serializer_to_json_schema(MySerializer, registry=registry)

    That fragment describes every ``MoneyField`` as a string of ``format:
    "money"`` in the schemas drfs' own helpers build. Declared on a spec as
    ``json_schema_registry=``, the same rule also reaches the spec as it
    compiles for the kernel, where only what the kernel can state is kept: the
    field is a string there and ``format: "money"``, which is none of the
    kernel's four formats, is dropped
    (test_the_compiled_schema_agrees_with_drfs_but_for_the_dropped_format).

    Attributes:
        fields: DRF ``Field`` subclasses, matched by ``isinstance`` when walking
            a serializer (``serializer_to_json_schema``, and the input side of
            ``spec_to_json_schema``).
        filters: ``django-filter`` ``Filter`` subclasses, matched by
            ``isinstance`` when walking a ``FilterSet``
            (``filterset_to_json_schema``).
        python_types: Bare Python types, matched by *identity* when walking a
            dataclass's field annotations, and, from a spec's
            ``json_schema_registry``, a selector's, target lookup's or spread
            service's annotations as the spec compiles.
    """

    fields: tuple[_Rule, ...] = ()
    filters: tuple[_Rule, ...] = ()
    python_types: tuple[_Rule, ...] = ()

    def extend(
        self,
        *,
        fields: Sequence[_Rule] = (),
        filters: Sequence[_Rule] = (),
        python_types: Sequence[_Rule] = (),
    ) -> JsonSchemaRegistry:
        """Return a new registry with the given rules prepended (they win first)."""
        return replace(
            self,
            fields=tuple(fields) + self.fields,
            filters=tuple(filters) + self.filters,
            python_types=tuple(python_types) + self.python_types,
        )


DEFAULT_JSON_SCHEMA_REGISTRY: JsonSchemaRegistry = JsonSchemaRegistry()
