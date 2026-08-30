"""Tests for ``spec_to_json_schema``."""

from __future__ import annotations

import dataclasses

import django_filters
import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers
from typing_extensions import NotRequired, TypedDict, Unpack

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


class _NestedRouteExtras(TypedDict):  # total=True
    parent_pk: int  # a required route capture
    label: NotRequired[str]


def test_selector_input_expands_unpack_extras_with_required() -> None:
    # A nested-route selector reading URL kwargs from ``**extras`` now advertises
    # them (``parent_pk`` required, ``label`` optional) instead of a hidden
    # KeyError; the inherited ``request`` / ``user`` seeds stay excluded.
    def _sel(user, request, **extras: Unpack[_NestedRouteExtras]): ...

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_sel)
    assert spec_to_json_schema(spec) == {
        "type": "object",
        "properties": {"parent_pk": {"type": "integer"}, "label": {"type": "string"}},
        "required": ["parent_pk"],
    }


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


def _selector_for_schema() -> object:
    def get_widget(user: object, pk: int) -> None: ...

    return get_widget


class TestMetadataIsInertApartFromTheReservedKey:
    """A project's own ``metadata`` keys must never reach a generated schema.

    ``spec_to_json_schema`` reads the one reserved ``"json_schema"`` key and
    otherwise names the fields it wants rather than enumerating the dataclass,
    so this holds by construction — pinned so a future field-walking
    implementation fails here instead of leaking a project's private
    declarations into an OpenAPI document or an MCP tool listing.
    """

    def test_service_input_and_output_are_identical(self) -> None:
        out = SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Out)
        bare = ServiceSpec(service=_service, input_serializer=_Create, output_selector_spec=out)
        declared = ServiceSpec(
            service=_service,
            input_serializer=_Create,
            output_selector_spec=out,
            metadata={"scope": "tenant", "audit": ["actor"]},
        )

        assert spec_to_json_schema(declared) == spec_to_json_schema(bare)
        assert spec_to_json_schema(declared, phase="output") == spec_to_json_schema(
            bare, phase="output"
        )

    def test_selector_input_and_output_are_identical(self) -> None:
        selector = _selector_for_schema()
        bare = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector, output_serializer=_Out)
        declared = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=selector,
            output_serializer=_Out,
            metadata={"scope": "tenant"},
        )

        assert spec_to_json_schema(declared) == spec_to_json_schema(bare)
        assert spec_to_json_schema(declared, phase="output") == spec_to_json_schema(
            bare, phase="output"
        )

    def test_nested_output_selector_metadata_is_inert_too(self) -> None:
        bare_out = SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Out)
        declared_out = SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=_Out, metadata={"scope": "tenant"}
        )
        bare = ServiceSpec(service=_service, output_selector_spec=bare_out)
        declared = ServiceSpec(service=_service, output_selector_spec=declared_out)

        assert spec_to_json_schema(declared, phase="output") == spec_to_json_schema(
            bare, phase="output"
        )


class _NestedIO(serializers.Serializer):
    id = serializers.IntegerField()
    inner = _Out()


class TestMaxDepth:
    """The bound reaches the two serializer-backed phases."""

    def test_service_input_is_bounded(self) -> None:
        spec = ServiceSpec(service=_service, input_serializer=_NestedIO)

        assert spec_to_json_schema(spec, max_depth=1) == {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "inner": {"type": "object"}},
            "required": ["id", "inner"],
        }

    def test_service_output_is_bounded_through_the_nested_selector_spec(self) -> None:
        spec = ServiceSpec(
            service=_service,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=_NestedIO
            ),
        )
        schema = spec_to_json_schema(spec, phase="output", max_depth=1)

        assert schema is not None
        assert schema["properties"]["inner"] == {"type": "object"}

    def test_selector_output_is_bounded(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_NestedIO)
        schema = spec_to_json_schema(spec, phase="output", max_depth=1)

        assert schema is not None
        assert schema["properties"]["inner"] == {"type": "object"}

    def test_unset_still_describes_every_level(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_NestedIO)
        schema = spec_to_json_schema(spec, phase="output")

        assert schema is not None
        assert schema["properties"]["inner"]["properties"]["id"] == {"type": "integer"}


class TestJsonSchemaMetadataFragment:
    """``metadata["json_schema"]`` is the one key generation reads.

    It is phase-keyed on purpose: a spec-level title and description belong to
    the *operation*, and merging one fragment into both phases would hang the
    operation's description off the output schema, which describes what comes
    back rather than what the caller sends.
    """

    def test_input_fragment_supplies_a_title_and_description(self) -> None:
        spec = ServiceSpec(
            service=_service,
            input_serializer=_Create,
            metadata={
                "json_schema": {
                    "input": {"title": "Archive project", "description": "Retire a project."}
                }
            },
        )
        assert spec_to_json_schema(spec) == {
            "type": "object",
            "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
            "required": ["name"],
            "title": "Archive project",
            "description": "Retire a project.",
        }

    def test_output_fragment_reaches_only_the_output_phase(self) -> None:
        out = SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Out)
        spec = ServiceSpec(
            service=_service,
            input_serializer=_Create,
            output_selector_spec=out,
            metadata={"json_schema": {"output": {"description": "The archived project."}}},
        )
        assert "description" not in spec_to_json_schema(spec)
        assert spec_to_json_schema(spec, phase="output") == {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
            "description": "The archived project.",
        }

    def test_a_selector_reads_its_own_fragment(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_get_widget,
            metadata={"json_schema": {"input": {"description": "Look a widget up by id."}}},
        )
        assert spec_to_json_schema(spec) == {
            "type": "object",
            "properties": {"pk": {"type": "integer"}},
            "description": "Look a widget up by id.",
        }

    def test_the_fragment_wins_over_a_derived_key(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_get_widget,
            metadata={"json_schema": {"input": {"properties": {"pk": {"type": "string"}}}}},
        )
        # Shallow: the fragment replaces the whole ``properties`` block rather
        # than merging into it, so there is one rule instead of a per-key one.
        assert spec_to_json_schema(spec)["properties"] == {"pk": {"type": "string"}}

    def test_an_output_fragment_does_not_conjure_an_undeclared_schema(self) -> None:
        spec = ServiceSpec(
            service=_service,
            metadata={"json_schema": {"output": {"description": "Nothing to describe."}}},
        )
        assert spec_to_json_schema(spec, phase="output") is None

    def test_the_other_phase_is_untouched(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_get_widget,
            output_serializer=_Out,
            metadata={"json_schema": {"input": {"title": "Widget"}}},
        )
        assert "title" not in (spec_to_json_schema(spec, phase="output") or {})

    def test_an_empty_declaration_changes_nothing(self) -> None:
        bare = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_get_widget)
        declared = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_get_widget, metadata={"json_schema": {}}
        )
        assert spec_to_json_schema(declared) == spec_to_json_schema(bare)

    def test_an_empty_phase_fragment_changes_nothing(self) -> None:
        bare = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_get_widget)
        declared = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_get_widget,
            metadata={"json_schema": {"input": {}}},
        )
        assert spec_to_json_schema(declared) == spec_to_json_schema(bare)

    def test_a_flat_fragment_is_refused_rather_than_silently_ignored(self) -> None:
        # The likely typo: forgetting the phase key. Ignoring it would publish
        # nothing and report nothing.
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_get_widget,
            metadata={"json_schema": {"title": "Widget"}},
        )
        with pytest.raises(ImproperlyConfigured, match="'title'"):
            spec_to_json_schema(spec)

    def test_a_non_mapping_declaration_is_refused(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_get_widget, metadata={"json_schema": "Widget"}
        )
        with pytest.raises(ImproperlyConfigured, match="must be a mapping"):
            spec_to_json_schema(spec)

    def test_a_non_mapping_phase_fragment_is_refused(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_get_widget,
            metadata={"json_schema": {"input": "Widget"}},
        )
        with pytest.raises(ImproperlyConfigured, match="must be a mapping"):
            spec_to_json_schema(spec)

    def test_a_declaration_is_read_off_the_spec_it_was_handed(self) -> None:
        # Never off the nested output selector: metadata does not inherit.
        out = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            output_serializer=_Out,
            metadata={"json_schema": {"output": {"title": "Nested"}}},
        )
        spec = ServiceSpec(service=_service, output_selector_spec=out)
        assert "title" not in (spec_to_json_schema(spec, phase="output") or {})
