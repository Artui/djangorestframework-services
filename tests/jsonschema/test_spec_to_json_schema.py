"""Tests for ``spec_to_json_schema``."""

from __future__ import annotations

import dataclasses

import django_filters
from rest_framework import serializers

from rest_framework_services.jsonschema.spec_to_json_schema import spec_to_json_schema
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


@dataclasses.dataclass
class _Create:
    name: str
    count: int = 0


class _Out(serializers.Serializer):
    id = serializers.IntegerField()


def _service(**_kwargs: object) -> None: ...


def test_service_input_reads_input_serializer() -> None:
    spec = ServiceSpec(service=_service, input_serializer=_Create)
    assert spec_to_json_schema(spec) == {
        "type": "object",
        "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
        "required": ["name"],
    }


def test_service_input_honours_partial() -> None:
    spec = ServiceSpec(service=_service, input_serializer=_Create, partial=True)
    assert "required" not in spec_to_json_schema(spec)


def test_service_input_without_serializer_is_empty_object() -> None:
    spec = ServiceSpec(service=_service)
    assert spec_to_json_schema(spec) == {"type": "object"}


def test_selector_input_is_empty_object() -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST)
    assert spec_to_json_schema(spec) == {"type": "object"}


def _get_widget(user, pk: int): ...


def test_selector_input_reflects_callable_params() -> None:
    # A retrieve selector now advertises `pk` (the transport seed `user` skipped).
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_get_widget)
    assert spec_to_json_schema(spec) == {
        "type": "object",
        "properties": {"pk": {"type": "integer"}},
    }


def test_selector_input_surfaces_unannotated_params_untyped() -> None:
    def _sel(user, pk): ...

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_sel)
    # `pk` is surfaced by name even without an annotation — just untyped.
    assert spec_to_json_schema(spec) == {"type": "object", "properties": {"pk": {}}}


def test_selector_input_skips_transport_seeds() -> None:
    def _sel(user, request, view, pk: int): ...

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_sel)
    assert spec_to_json_schema(spec)["properties"] == {"pk": {"type": "integer"}}


def test_selector_input_skips_var_args_and_kwargs() -> None:
    def _sel(user, *args, **kwargs): ...

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_sel)
    assert spec_to_json_schema(spec) == {"type": "object"}


def test_selector_input_with_unresolvable_annotation_stays_untyped() -> None:
    def _sel(user, pk: Ghost): ...  # noqa: F821 — deliberately unresolvable forward ref

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_sel)
    assert spec_to_json_schema(spec) == {"type": "object", "properties": {"pk": {}}}


def test_selector_input_merges_callable_params_and_filter_set() -> None:
    class _FS(django_filters.FilterSet):
        name = django_filters.CharFilter()

    def _sel(user, pk: int): ...

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_sel, filter_set=_FS)
    assert spec_to_json_schema(spec) == {
        "type": "object",
        "properties": {"pk": {"type": "integer"}, "name": {"type": "string"}},
    }


def test_filter_set_field_wins_over_callable_param_of_same_name() -> None:
    class _FS(django_filters.FilterSet):
        status = django_filters.NumberFilter()

    def _sel(user, status: str): ...

    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_sel, filter_set=_FS)
    # The declared filter is the more precise source for the shared name.
    assert spec_to_json_schema(spec)["properties"]["status"] == {"type": "number"}


def test_selector_input_merges_filter_set_properties() -> None:
    class _FS(django_filters.FilterSet):
        name = django_filters.CharFilter()

    spec = SelectorSpec(kind=SelectorKind.LIST, filter_set=_FS)
    assert spec_to_json_schema(spec) == {
        "type": "object",
        "properties": {"name": {"type": "string"}},
    }


def test_selector_input_with_empty_filter_set_stays_bare_object() -> None:
    class _FS(django_filters.FilterSet): ...

    spec = SelectorSpec(kind=SelectorKind.LIST, filter_set=_FS)
    assert spec_to_json_schema(spec) == {"type": "object"}


def test_service_output_reads_nested_output_selector_spec() -> None:
    spec = ServiceSpec(
        service=_service,
        output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Out),
    )
    assert spec_to_json_schema(spec, phase="output") == {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    }


def test_service_output_list_kind_is_array() -> None:
    spec = ServiceSpec(
        service=_service,
        output_selector_spec=SelectorSpec(kind=SelectorKind.LIST, output_serializer=_Out),
    )
    schema = spec_to_json_schema(spec, phase="output")
    assert schema is not None
    assert schema["type"] == "array"


def test_service_output_without_selector_spec_is_none() -> None:
    spec = ServiceSpec(service=_service)
    assert spec_to_json_schema(spec, phase="output") is None


def test_selector_output_reads_own_serializer_and_kind() -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, output_serializer=_Out)
    schema = spec_to_json_schema(spec, phase="output")
    assert schema == {
        "type": "array",
        "items": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    }


def test_selector_output_retrieve_kind_is_item() -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Out)
    assert spec_to_json_schema(spec, phase="output") == {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    }


def test_selector_output_without_serializer_is_none() -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE)
    assert spec_to_json_schema(spec, phase="output") is None


def test_registry_is_forwarded_to_service_input() -> None:
    from rest_framework_services.types.json_schema_registry import DEFAULT_JSON_SCHEMA_REGISTRY

    class _MoneyField(serializers.Field): ...

    class _Order(serializers.Serializer):
        total = _MoneyField()

    spec = ServiceSpec(service=_service, input_serializer=_Order)
    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
        fields=[(_MoneyField, {"type": "string", "format": "money"})]
    )
    schema = spec_to_json_schema(spec, registry=registry)
    assert schema is not None
    assert schema["properties"]["total"] == {"type": "string", "format": "money"}


def test_registry_is_forwarded_to_selector_filter() -> None:
    from rest_framework_services.types.json_schema_registry import DEFAULT_JSON_SCHEMA_REGISTRY

    class _RefFilter(django_filters.Filter): ...

    class _FS(django_filters.FilterSet):
        ref = _RefFilter()

    spec = SelectorSpec(kind=SelectorKind.LIST, filter_set=_FS)
    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
        filters=[(_RefFilter, {"type": "string", "format": "ref"})]
    )
    assert spec_to_json_schema(spec, registry=registry) == {
        "type": "object",
        "properties": {"ref": {"type": "string", "format": "ref"}},
    }
