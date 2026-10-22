"""``spec_to_json_schema`` reads a spec's own ``json_schema_registry`` when none is passed."""

from __future__ import annotations

from typing import Any

import django_filters
from rest_framework import serializers

from rest_framework_services.jsonschema.spec_to_json_schema import spec_to_json_schema
from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from tests.testapp.models import Post


class _MoneyField(serializers.CharField): ...


class Money: ...


class _In(serializers.Serializer):
    amount = _MoneyField()


class _Out(serializers.Serializer):
    amount = _MoneyField()


def _service(**_kwargs: object) -> None: ...


def _select(user: Any, total: Money) -> None: ...


_MONEY = {"type": "string", "format": "money"}
_CENTS = {"type": "string", "format": "cents"}

_SPEC_S = JsonSchemaRegistry(
    fields=((_MoneyField, _MONEY),),
    python_types=((Money, {"type": "string", "pattern": "^[0-9]+$"}),),
)
_NESTED_S = JsonSchemaRegistry(fields=((_MoneyField, {"type": "string", "format": "nested"}),))
_PASSED = JsonSchemaRegistry(fields=((_MoneyField, _CENTS),))


def _amount(schema: dict[str, Any] | None) -> dict[str, Any]:
    assert schema is not None
    return schema["properties"]["amount"]


def test_a_service_spec_s_input_reads_its_registry() -> None:
    spec = ServiceSpec(service=_service, input_serializer=_In, json_schema_registry=_SPEC_S)
    assert _amount(spec_to_json_schema(spec)) == _MONEY


def test_a_selector_spec_s_reads_take_its_python_type_rules() -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_select, json_schema_registry=_SPEC_S)
    schema = spec_to_json_schema(spec)
    assert schema is not None
    assert schema["properties"]["total"] == {"type": "string", "pattern": "^[0-9]+$"}


def test_a_spec_with_no_registry_reads_the_empty_default() -> None:
    spec = ServiceSpec(service=_service, input_serializer=_In)
    assert _amount(spec_to_json_schema(spec)) == {"type": "string"}


def test_a_passed_registry_wins_over_the_spec_s() -> None:
    spec = ServiceSpec(service=_service, input_serializer=_In, json_schema_registry=_SPEC_S)
    assert _amount(spec_to_json_schema(spec, registry=_PASSED)) == _CENTS
    # The empty default, passed, is a registry passed: it wins too.
    assert _amount(spec_to_json_schema(spec, registry=DEFAULT_JSON_SCHEMA_REGISTRY)) == {
        "type": "string"
    }


def _output_spec(nested_registry: JsonSchemaRegistry | None) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=_service,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=_Out, json_schema_registry=nested_registry
        ),
        json_schema_registry=_SPEC_S,
    )


def test_an_output_selector_spec_reads_its_own_registry() -> None:
    schema = spec_to_json_schema(_output_spec(_NESTED_S), phase="output")
    assert _amount(schema) == {"type": "string", "format": "nested"}


def test_an_output_selector_spec_with_none_reads_the_outer_spec_s() -> None:
    assert _amount(spec_to_json_schema(_output_spec(None), phase="output")) == _MONEY


def test_a_passed_registry_wins_over_a_nested_spec_s() -> None:
    schema = spec_to_json_schema(_output_spec(_NESTED_S), phase="output", registry=_PASSED)
    assert _amount(schema) == _CENTS


def test_a_selector_spec_s_output_reads_its_own_registry() -> None:
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_select,
        output_serializer=_Out,
        json_schema_registry=_SPEC_S,
    )
    assert _amount(spec_to_json_schema(spec, phase="output")) == _MONEY


class _Shelf(django_filters.FilterSet):
    title = django_filters.CharFilter()

    class Meta:
        model = Post
        fields = ("title",)


def _rows(**_kwargs: Any) -> Any:
    return Post.objects.all()


def test_a_filters_rule_reaches_drfs_schema_and_not_the_compiled_spec() -> None:
    registry = JsonSchemaRegistry(
        filters=((django_filters.CharFilter, {"type": "string", "pattern": "^[a-z]+$"}),)
    )
    spec = SelectorSpec(
        kind=SelectorKind.LIST, selector=_rows, filter_set=_Shelf, json_schema_registry=registry
    )
    schema = spec_to_json_schema(spec)
    assert schema is not None
    assert schema["properties"]["title"]["pattern"] == "^[a-z]+$"
    compiled = spec.as_kernel_spec().parameters().get("title")
    assert compiled is not None
    assert compiled.json_schema_extra is None
