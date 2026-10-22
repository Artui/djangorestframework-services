"""A spec's ``json_schema_registry`` reaching the Validator and the presenters it compiles."""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any

from django_service_specs.output.output import OutputField
from django_service_specs.parameters.parameters import Parameter
from django_service_specs.schema.parameters_schema import parameters_schema
from django_service_specs.specs.kernel_spec import kernel_spec
from django_service_specs.types.unknown_arguments import UnknownArguments
from rest_framework import serializers

from rest_framework_services.jsonschema.spec_to_json_schema import spec_to_json_schema
from rest_framework_services.service_specs.kernel_spec_variant import kernel_spec_variant
from rest_framework_services.types.json_schema_registry import JsonSchemaRegistry
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


class _MoneyField(serializers.CharField): ...


class _In(serializers.Serializer):
    amount = _MoneyField()
    other = serializers.CharField(required=False)


class _Out(serializers.Serializer):
    amount = _MoneyField()


_MONEY = {"type": "string", "format": "money", "pattern": "^[0-9]+$"}
_REGISTRY = JsonSchemaRegistry(fields=((_MoneyField, _MONEY),))
_OWN = JsonSchemaRegistry(fields=((_MoneyField, {"type": "integer"}),))


def _service(**_kwargs: Any) -> None: ...


def _select(**_kwargs: Any) -> None: ...


def _amount_parameter(compiled: Any) -> Parameter:
    parameter = compiled.parameters().get("amount")
    assert parameter is not None
    return parameter


def _amount_field(presenter: Any) -> OutputField:
    (field,) = presenter.output()
    return field


def test_a_compiled_spec_s_input_fields_take_its_rules() -> None:
    spec = ServiceSpec(service=_service, input_serializer=_In, json_schema_registry=_REGISTRY)
    parameter = _amount_parameter(kernel_spec(spec))
    assert (parameter.type, parameter.format) == ("string", None)
    assert parameter.json_schema_extra == {"pattern": "^[0-9]+$"}


def test_the_compiled_schema_agrees_with_drfs_but_for_the_dropped_format() -> None:
    spec = ServiceSpec(service=_service, input_serializer=_In, json_schema_registry=_REGISTRY)
    ours = parameters_schema(
        kernel_spec(spec).parameters(), unknown_arguments=UnknownArguments.IGNORE
    )["properties"]["amount"]
    schema = spec_to_json_schema(spec)
    assert schema is not None
    theirs = schema["properties"]["amount"]
    assert theirs["format"] == "money"
    assert "format" not in ours
    assert ours == {key: value for key, value in theirs.items() if key != "format"}


def test_an_excluded_variant_keeps_the_spec_s_rules() -> None:
    spec = ServiceSpec(service=_service, input_serializer=_In, json_schema_registry=_REGISTRY)
    parameter = _amount_parameter(kernel_spec_variant(spec, excluded=frozenset({"other"})))
    assert parameter.json_schema_extra == {"pattern": "^[0-9]+$"}


def test_a_compiled_spec_s_output_fields_take_its_rules() -> None:
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_select,
        output_serializer=_Out,
        json_schema_registry=_REGISTRY,
    )
    field = _amount_field(kernel_spec(spec).presenter)
    assert (field.type, field.format, field.json_schema_extra) == (
        "string",
        None,
        {"pattern": "^[0-9]+$"},
    )


def _with_output(own: JsonSchemaRegistry | None, *, re_reads: bool) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=_service,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_select if re_reads else None,
            output_serializer=_Out,
            json_schema_registry=own,
        ),
        json_schema_registry=_REGISTRY,
    )


def test_a_re_reading_output_reads_its_own_registry_else_the_service_spec_s() -> None:
    own = kernel_spec(_with_output(_OWN, re_reads=True)).output_selector_spec
    inherited = kernel_spec(_with_output(None, re_reads=True)).output_selector_spec
    assert _amount_field(own.presenter).type == "integer"
    assert _amount_field(inherited.presenter).json_schema_extra == {"pattern": "^[0-9]+$"}


def test_a_selector_less_output_reads_its_own_registry_else_the_service_spec_s() -> None:
    own = kernel_spec(_with_output(_OWN, re_reads=False))
    inherited = kernel_spec(_with_output(None, re_reads=False))
    assert _amount_field(own.presenter).type == "integer"
    assert _amount_field(inherited.presenter).json_schema_extra == {"pattern": "^[0-9]+$"}


# A rule the kernel can state only part of: no type, so neither the format nor
# the bound has a seat on the compiled field.
_PARTLY = JsonSchemaRegistry(
    fields=((_MoneyField, {"format": "date", "minimum": 1, "pattern": "^[0-9]+$"}),)
)


def test_a_rule_the_kernel_cannot_state_whole_still_compiles() -> None:
    spec = ServiceSpec(
        service=_service,
        input_serializer=_In,
        output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Out),
        json_schema_registry=_PARTLY,
    )
    compiled = kernel_spec(spec)
    parameter = _amount_parameter(compiled)
    assert (parameter.type, parameter.format, parameter.minimum) == ("any", None, None)
    assert parameter.json_schema_extra == {"pattern": "^[0-9]+$"}
    # An output field has no bound to set, so the bound is advertised there.
    field = _amount_field(compiled.presenter)
    assert (field.type, field.format) == ("any", None)
    assert field.json_schema_extra == {"minimum": 1, "pattern": "^[0-9]+$"}
    # drfs' own schema keeps describing the whole fragment.
    schema = spec_to_json_schema(spec)
    assert schema is not None
    assert schema["properties"]["amount"] == {"format": "date", "minimum": 1, "pattern": "^[0-9]+$"}


@dataclasses.dataclass
class _Order:
    amount: Decimal


def test_a_dataclass_input_takes_fields_rules_in_the_compile_and_python_types_in_drfs() -> None:
    by_type = JsonSchemaRegistry(python_types=((Decimal, {"type": "string", "pattern": "^1"}),))
    spec = ServiceSpec(service=_service, input_serializer=_Order, json_schema_registry=by_type)
    schema = spec_to_json_schema(spec)
    assert schema is not None
    assert schema["properties"]["amount"] == {"type": "string", "pattern": "^1"}
    # The compile walks the DataclassSerializer's fields, so the type's rule is
    # not read there ...
    parameter = _amount_parameter(kernel_spec(spec))
    assert (parameter.format, parameter.json_schema_extra) == ("decimal", None)
    # ... and a rule on the field class the type is read through is.
    by_field = JsonSchemaRegistry(
        fields=((serializers.DecimalField, {"type": "string", "pattern": "^1"}),)
    )
    spec = ServiceSpec(service=_service, input_serializer=_Order, json_schema_registry=by_field)
    assert _amount_parameter(kernel_spec(spec)).json_schema_extra == {"pattern": "^1"}
