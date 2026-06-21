"""Tests for ``JsonSchemaRegistry``."""

from __future__ import annotations

from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)


def test_default_registry_is_empty() -> None:
    assert DEFAULT_JSON_SCHEMA_REGISTRY.fields == ()
    assert DEFAULT_JSON_SCHEMA_REGISTRY.filters == ()
    assert DEFAULT_JSON_SCHEMA_REGISTRY.python_types == ()


def test_extend_prepends_rules_across_all_three_lists() -> None:
    base = JsonSchemaRegistry(fields=((int, {"type": "integer"}),))
    extended = base.extend(
        fields=[(str, {"type": "string"})],
        filters=[(float, {"type": "number"})],
        python_types=[(bool, {"type": "boolean"})],
    )
    # Consumer rules are prepended (they win first).
    assert extended.fields == ((str, {"type": "string"}), (int, {"type": "integer"}))
    assert extended.filters == ((float, {"type": "number"}),)
    assert extended.python_types == ((bool, {"type": "boolean"}),)


def test_extend_does_not_mutate_the_original() -> None:
    base = JsonSchemaRegistry(fields=((int, {"type": "integer"}),))
    base.extend(fields=[(str, {"type": "string"})])
    assert base.fields == ((int, {"type": "integer"}),)
    assert base.filters == ()
    assert base.python_types == ()


def test_extend_with_no_rules_is_an_equal_registry() -> None:
    base = DEFAULT_JSON_SCHEMA_REGISTRY.extend(fields=[(int, {"type": "integer"})])
    assert base.extend() == base
