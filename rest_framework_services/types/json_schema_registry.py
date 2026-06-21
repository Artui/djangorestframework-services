"""``JsonSchemaRegistry`` — consumer-extensible type → JSON Schema mappings.

``DEFAULT_JSON_SCHEMA_REGISTRY`` is the empty base every ``*_to_json_schema``
helper falls back to; layer your own rules on with :meth:`JsonSchemaRegistry.extend`
and pass the result via the helpers' ``registry=`` argument.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

# A single mapping rule: ``(type, schema_fragment)``. The fragment is copied
# per use, so a matched consumer dict is never mutated by the walkers.
_Rule = tuple[type, dict[str, Any]]


@dataclass(frozen=True)
class JsonSchemaRegistry:
    """Consumer-extensible ``type → JSON Schema fragment`` rules for the helpers.

    Three ordered rule lists, each a sequence of ``(type, schema_fragment)``:

    - ``fields`` — DRF ``Field`` subclasses, matched by ``isinstance`` when
      walking a serializer (``serializer_to_json_schema`` / the input side of
      ``spec_to_json_schema``).
    - ``filters`` — ``django-filter`` ``Filter`` subclasses, matched by
      ``isinstance`` when walking a ``FilterSet`` (``filterset_to_json_schema``).
    - ``python_types`` — bare Python types, matched by *identity* when walking a
      dataclass's field annotations.

    Rules are tried in order and the first match wins, **before** the built-in
    mappings — so a rule both adds support for a custom type and can override a
    built-in. The matched fragment is copied per use, so callers may freely
    mutate the returned schema.

    Immutable: :meth:`extend` returns a *new* registry rather than mutating, so
    there is no shared global state to leak across callers or tests. Start from
    :data:`DEFAULT_JSON_SCHEMA_REGISTRY` (empty) and layer rules on::

        registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
            fields=[(MoneyField, {"type": "string", "format": "money"})],
        )
        schema = serializer_to_json_schema(MySerializer, registry=registry)

    The built-in mappings are intentionally *not* held here — they live with the
    walkers (and the filter built-ins import ``django-filter`` lazily). A
    registry carries only consumer additions, so the empty default stays
    dependency-free.
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
