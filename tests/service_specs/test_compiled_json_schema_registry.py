"""A spec's ``json_schema_registry`` where the compile reads a callable's annotations."""

from __future__ import annotations

from typing import Annotated, Any

from django_service_specs.parameters.parameters import Parameter
from django_service_specs.specs.kernel_spec import kernel_spec

from rest_framework_services.service_specs.kernel_spec_variant import kernel_spec_variant
from rest_framework_services.types.input_description import InputDescription
from rest_framework_services.types.json_schema_registry import JsonSchemaRegistry
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


class Money: ...


_REGISTRY = JsonSchemaRegistry(
    python_types=(
        (
            Money,
            {"type": "string", "format": "money", "pattern": "^[0-9]+$", "description": "Cents."},
        ),
    )
)
_OTHER = JsonSchemaRegistry(python_types=((Money, {"type": "integer", "minimum": 0}),))


def _select(
    user: Any,
    total: Money,
    maybe: Money | None = None,
    described: Annotated[Money, InputDescription("Own.")] | None = None,
    listed: list[Money] | None = None,
) -> None: ...


def _lookup(pk: Money) -> None: ...


def _service(**_kwargs: Any) -> None: ...


def _spread(total: Money) -> None: ...


def _reads(compiled: Any) -> dict[str, Parameter]:
    return {parameter.name: parameter for parameter in compiled.reads}


def test_a_selector_spec_s_reads_take_its_python_type_rules() -> None:
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_select, json_schema_registry=_REGISTRY
    )
    reads = _reads(spec.as_kernel_spec())
    assert reads["total"].type == "string"
    assert reads["total"].json_schema_extra == {"pattern": "^[0-9]+$"}
    assert reads["total"].help == "Cents."
    # The format outside the kernel's four is dropped here, and only here.
    assert reads["total"].format is None
    assert (reads["maybe"].type, reads["maybe"].nullable) == ("string", True)
    # An author's description wins over the rule's.
    assert reads["described"].help == "Own."
    assert reads["listed"].items == "string"


def test_without_a_registry_a_class_no_parameter_type_names_is_any() -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_select)
    assert _reads(spec.as_kernel_spec())["total"].type == "any"


def _with_lookup(own: JsonSchemaRegistry | None) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=_service,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_lookup, json_schema_registry=own
        ),
        json_schema_registry=_REGISTRY,
    )


def test_a_target_lookup_reads_its_own_registry() -> None:
    compiled = kernel_spec(_with_lookup(_OTHER))
    assert _reads(compiled.instance_selector_spec)["pk"].type == "integer"


def test_a_target_lookup_with_none_reads_the_service_spec_s() -> None:
    compiled = kernel_spec(_with_lookup(None))
    assert _reads(compiled.instance_selector_spec)["pk"].type == "string"


def test_a_spread_service_s_parameters_take_its_rules() -> None:
    spec = ServiceSpec(service=_spread, json_schema_registry=_OTHER)
    (parameter,) = kernel_spec_variant(spec, spread=True).parameters()
    assert (parameter.name, parameter.type, parameter.minimum) == ("total", "integer", 0)


def test_an_affordance_reads_only_its_own_registry() -> None:
    affordance = ServiceSpec(
        service=_service,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
    )
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_select,
        affordances={"open": affordance},
        json_schema_registry=_REGISTRY,
    )
    spec.as_kernel_spec()
    assert _reads(kernel_spec(affordance).instance_selector_spec)["pk"].type == "any"
