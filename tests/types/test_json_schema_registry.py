"""Tests for ``JsonSchemaRegistry``."""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


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


def _service(**_kwargs: object) -> None: ...


def _select(**_kwargs: object) -> None: ...


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda **kw: ServiceSpec(service=_service, **kw), id="service-spec"),
        pytest.param(
            lambda **kw: SelectorSpec(kind=SelectorKind.LIST, selector=_select, **kw),
            id="selector-spec",
        ),
    ],
)
def test_a_spec_declares_no_registry_by_default(build: Any) -> None:
    assert build().json_schema_registry is None
    registry = JsonSchemaRegistry(fields=((int, {"type": "integer"}),))
    assert build(json_schema_registry=registry).json_schema_registry is registry


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda **kw: ServiceSpec(service=_service, **kw), id="service-spec"),
        pytest.param(
            lambda **kw: SelectorSpec(kind=SelectorKind.LIST, selector=_select, **kw),
            id="selector-spec",
        ),
    ],
)
def test_a_spec_carrying_a_registry_still_hashes(build: Any) -> None:
    """A rule's fragment is a dict, so the field stays out of the hash, as the kernel's ``json_schema_extra`` does."""
    registry = JsonSchemaRegistry(fields=((int, {"type": "integer"}),))
    assert hash(build(json_schema_registry=registry)) == hash(build())
    assert build(json_schema_registry=registry) != build()
