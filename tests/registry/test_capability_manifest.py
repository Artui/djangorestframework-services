"""Tests for ``capability_manifest``."""

from __future__ import annotations

import json
from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.permissions import BasePermission

from rest_framework_services import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    capability_manifest,
    spec_to_json_schema,
)


class _IsSupport(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return True


class _IsOwner(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return True


class _RefundInput(serializers.Serializer):
    amount = serializers.IntegerField()


class _OrderOutput(serializers.Serializer):
    id = serializers.IntegerField()


class _Money(serializers.Field):
    """A custom field only a consumer rule can describe."""


class _MoneyInput(serializers.Serializer):
    total = _Money()


def _refund(**_: Any) -> None: ...


def _orders(*, user: Any, status: str) -> list[Any]:
    return []


def _refund_spec(**overrides: Any) -> ServiceSpec[Any, Any, Any]:
    fields: dict[str, Any] = {
        "service": _refund,
        "input_serializer": _RefundInput,
        "output_selector_spec": SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=_OrderOutput
        ),
        "idempotent": True,
        "permission_classes": [_IsSupport],
    }
    fields.update(overrides)
    return ServiceSpec(**fields)


def _orders_spec(**overrides: Any) -> SelectorSpec[Any, Any]:
    fields: dict[str, Any] = {
        "kind": SelectorKind.LIST,
        "selector": _orders,
        "output_serializer": _OrderOutput,
    }
    fields.update(overrides)
    return SelectorSpec(**fields)


def _registry(*entries: tuple[str, Any, tuple[str, ...]]) -> SpecRegistry:
    registry = SpecRegistry()
    for name, spec, tags in entries:
        registry.register(name, spec, tags=tags)
    return registry


_GUARD_PATH = f"{__name__}._IsSupport"


# --- the document ----------------------------------------------------------


def test_the_whole_document_as_served() -> None:
    """The literal a reader receives -- not a round trip through our own helpers."""
    registry = _registry(
        ("refund_order", _refund_spec(), ("write", "admin")),
        ("list_orders", _orders_spec(), ("read",)),
    )

    assert capability_manifest(registry) == {
        "version": 1,
        "dialect": "rest_framework_services.jsonschema/1",
        "operations": [
            {
                "name": "refund_order",
                "kind": "mutation",
                "tags": ["admin", "write"],
                "idempotent": True,
                "input_schema": {
                    "type": "object",
                    "properties": {"amount": {"type": "integer"}},
                    "required": ["amount"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                },
                "guards": [{"source": _GUARD_PATH}],
            },
            {
                "name": "list_orders",
                "kind": "query",
                "tags": ["read"],
                "idempotent": None,
                "input_schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                },
                "output_schema": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                        "required": ["id"],
                    },
                },
                "guards": None,
            },
        ],
        "unguarded": ["list_orders"],
    }


def test_the_document_survives_json_unchanged() -> None:
    """JSON-native all the way down, so any transport can put it on a wire."""
    manifest = capability_manifest(
        _registry(("refund_order", _refund_spec(), ("write",)), ("list", _orders_spec(), ()))
    )
    assert json.loads(json.dumps(manifest)) == manifest


def test_an_empty_registry_is_an_empty_document_not_an_error() -> None:
    assert capability_manifest(SpecRegistry()) == {
        "version": 1,
        "dialect": "rest_framework_services.jsonschema/1",
        "operations": [],
        "unguarded": [],
    }


@pytest.mark.django_db
def test_building_it_reads_no_database(django_assert_num_queries: Any) -> None:
    registry = _registry(("refund_order", _refund_spec(), ()), ("list", _orders_spec(), ()))
    with django_assert_num_queries(0):
        capability_manifest(registry)


# --- per-operation keys -----------------------------------------------------


def test_operations_keep_registration_order() -> None:
    registry = _registry(
        ("b_second_alphabetically", _orders_spec(), ()),
        ("a_first_alphabetically", _refund_spec(), ()),
    )
    names = [op["name"] for op in capability_manifest(registry)["operations"]]
    assert names == ["b_second_alphabetically", "a_first_alphabetically"]


def test_kind_follows_the_spec_type() -> None:
    registry = _registry(("write", _refund_spec(), ()), ("read", _orders_spec(), ()))
    kinds = {op["name"]: op["kind"] for op in capability_manifest(registry)["operations"]}
    assert kinds == {"write": "mutation", "read": "query"}


def test_tags_are_sorted_so_two_builds_are_identical() -> None:
    # Eight tags, because a frozenset's iteration order follows string hashing,
    # which is randomised per process: with three, an unsorted build matches the
    # sorted order often enough to let a regression through one run in six.
    tags = ("zeta", "alpha", "mid", "omega", "beta", "kappa", "delta", "gamma")
    registry = _registry(("op", _orders_spec(), tags))
    assert capability_manifest(registry)["operations"][0]["tags"] == sorted(tags)


@pytest.mark.parametrize("declared", [True, False, None])
def test_idempotent_is_reported_as_declared_silence_included(declared: bool | None) -> None:
    registry = _registry(("op", _refund_spec(idempotent=declared), ()))
    assert capability_manifest(registry)["operations"][0]["idempotent"] is declared


def test_a_query_reports_no_idempotency_claim() -> None:
    registry = _registry(("op", _orders_spec(), ()))
    assert capability_manifest(registry)["operations"][0]["idempotent"] is None


def test_schemas_are_the_kernel_derivation_verbatim() -> None:
    """One dialect: the manifest never re-derives what ``spec_to_json_schema`` says."""
    refund = _refund_spec()
    orders = _orders_spec()
    operations = capability_manifest(_registry(("r", refund, ()), ("o", orders, ())))["operations"]

    for operation, spec in zip(operations, (refund, orders), strict=True):
        assert operation["input_schema"] == spec_to_json_schema(spec, phase="input")
        assert operation["output_schema"] == spec_to_json_schema(spec, phase="output")


def test_an_undeclared_output_is_none_rather_than_a_fabricated_shape() -> None:
    registry = _registry(("op", _refund_spec(output_selector_spec=None), ()))
    assert capability_manifest(registry)["operations"][0]["output_schema"] is None


def test_schema_registry_reaches_the_derivation() -> None:
    """A consumer's custom-field rule must describe the field here exactly as it
    does for the transport that forwards the same registry."""
    rules = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
        fields=[(_Money, {"type": "string", "format": "money"})]
    )
    registry = _registry(("op", _refund_spec(input_serializer=_MoneyInput), ()))

    operation = capability_manifest(registry, schema_registry=rules)["operations"][0]

    assert operation["input_schema"]["properties"]["total"] == {
        "type": "string",
        "format": "money",
    }


# --- guards -----------------------------------------------------------------


def test_guards_name_each_class_by_import_path_in_declaration_order() -> None:
    registry = _registry(("op", _refund_spec(permission_classes=[_IsOwner, _IsSupport]), ()))
    assert capability_manifest(registry)["operations"][0]["guards"] == [
        {"source": f"{__name__}._IsOwner"},
        {"source": _GUARD_PATH},
    ]


def test_undeclared_guards_are_none_and_a_declared_empty_list_stays_empty() -> None:
    registry = _registry(
        ("inherits", _refund_spec(permission_classes=None), ()),
        ("open_on_purpose", _refund_spec(permission_classes=[]), ()),
    )
    guards = {op["name"]: op["guards"] for op in capability_manifest(registry)["operations"]}
    assert guards == {"inherits": None, "open_on_purpose": []}


def test_unguarded_names_only_the_undeclared_in_registration_order() -> None:
    registry = _registry(
        ("second_open", _orders_spec(), ()),
        ("guarded", _refund_spec(), ()),
        ("open_on_purpose", _refund_spec(permission_classes=[]), ()),
        ("first_open", _refund_spec(permission_classes=None), ()),
    )
    assert capability_manifest(registry)["unguarded"] == ["second_open", "first_open"]


# --- refusals ---------------------------------------------------------------


def test_a_bare_mapping_is_refused_because_it_carries_no_tags() -> None:
    with pytest.raises(TypeError, match="carries no tags"):
        capability_manifest({"op": _refund_spec()})
