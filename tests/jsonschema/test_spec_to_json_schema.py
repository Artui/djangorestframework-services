"""Tests for ``spec_to_json_schema``."""

from __future__ import annotations

import dataclasses
from typing import Any

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


class _NotASerializer: ...


def test_an_unwalkable_selector_output_is_refused_through_the_spec() -> None:
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda: None,
        output_serializer=_NotASerializer,  # ty: ignore[invalid-argument-type]
    )
    with pytest.raises(TypeError, match="Cannot derive an output JSON Schema"):
        spec_to_json_schema(spec, phase="output")


def test_an_unwalkable_output_behind_a_service_is_refused_through_the_spec() -> None:
    nested = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda: None,
        output_serializer=_NotASerializer,  # ty: ignore[invalid-argument-type]
    )
    spec = ServiceSpec(service=lambda: None, output_selector_spec=nested)
    with pytest.raises(TypeError, match="Cannot derive an output JSON Schema"):
        spec_to_json_schema(spec, phase="output")


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


def test_a_many_service_output_is_an_array_whatever_its_selector_kind() -> None:
    # A bulk spec renders through a ``RETRIEVE`` output selector by convention --
    # its kind describes one row -- and the result is still the list, rendered
    # item by item. Reading the kind alone described an object for a payload that
    # is always an array.
    spec = ServiceSpec(
        service=_service,
        many=True,
        output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Out),
    )
    assert spec_to_json_schema(spec, phase="output") == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    }


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


class _Line(serializers.Serializer):
    sku = serializers.CharField()
    quantity = serializers.IntegerField()


class _NonEmptyLines(serializers.ListSerializer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_empty", False)
        super().__init__(*args, **kwargs)


class _NonEmptyLine(_Line):
    class Meta:
        list_serializer_class = _NonEmptyLines


class _BoundedLines(serializers.ListSerializer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("min_length", 2)
        kwargs.setdefault("max_length", 5)
        super().__init__(*args, **kwargs)


class _BoundedLine(_Line):
    class Meta:
        list_serializer_class = _BoundedLines


class _NonEmptyShortLines(serializers.ListSerializer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_empty", False)
        kwargs.setdefault("min_length", 0)
        kwargs.setdefault("max_length", 0)
        super().__init__(*args, **kwargs)


class _NonEmptyShortLine(_Line):
    class Meta:
        list_serializer_class = _NonEmptyShortLines


class _LinesReadingTheRequest(serializers.ListSerializer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Routine over HTTP, where the key is always there.
        self.request = self.context["request"]


class _LineReadingTheRequest(_Line):
    class Meta:
        list_serializer_class = _LinesReadingTheRequest


_LINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"sku": {"type": "string"}, "quantity": {"type": "integer"}},
    "required": ["sku", "quantity"],
}


class TestManyInput:
    """A ``many=True`` spec takes its list under one argument, because a caller whose
    input is an object of named arguments cannot send a bare array."""

    def test_the_list_is_wrapped_under_the_default_argument(self) -> None:
        spec = ServiceSpec(service=_service, input_serializer=_Line, many=True)
        assert spec_to_json_schema(spec) == {
            "type": "object",
            "properties": {"items": {"type": "array", "items": _LINE_SCHEMA}},
            "required": ["items"],
            "additionalProperties": False,
        }

    def test_a_declared_argument_names_the_property(self) -> None:
        spec = ServiceSpec(
            service=_service, input_serializer=_Line, many=True, many_argument="lines"
        )
        schema = spec_to_json_schema(spec)
        assert schema is not None
        assert list(schema["properties"]) == ["lines"]
        assert schema["required"] == ["lines"]

    def test_a_single_item_spec_is_not_wrapped(self) -> None:
        spec = ServiceSpec(service=_service, input_serializer=_Line)
        assert spec_to_json_schema(spec) == _LINE_SCHEMA

    def test_a_dataclass_item_is_wrapped(self) -> None:
        spec = ServiceSpec(service=_service, input_serializer=_Create, many=True)
        schema = spec_to_json_schema(spec)
        assert schema is not None
        assert schema["properties"]["items"] == {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
                "required": ["name"],
            },
        }

    def test_no_input_serializer_is_a_list_of_objects(self) -> None:
        spec = ServiceSpec(service=_service, many=True)
        schema = spec_to_json_schema(spec)
        assert schema is not None
        assert schema["properties"]["items"] == {"type": "array", "items": {"type": "object"}}

    def test_partial_relaxes_the_item_and_never_the_argument(self) -> None:
        """The list itself is the payload, and DRF refuses a missing one under partial too."""
        spec = ServiceSpec(service=_service, input_serializer=_Line, many=True, partial=True)
        schema = spec_to_json_schema(spec)
        assert schema is not None
        assert "required" not in schema["properties"]["items"]["items"]
        assert schema["required"] == ["items"]

    def test_a_list_that_may_not_be_empty_has_a_minimum_of_one(self) -> None:
        spec = ServiceSpec(service=_service, input_serializer=_NonEmptyLine, many=True)
        schema = spec_to_json_schema(spec)
        assert schema is not None
        assert schema["properties"]["items"]["minItems"] == 1
        assert "maxItems" not in schema["properties"]["items"]

    def test_length_bounds_are_carried(self) -> None:
        spec = ServiceSpec(service=_service, input_serializer=_BoundedLine, many=True)
        schema = spec_to_json_schema(spec)
        assert schema is not None
        assert schema["properties"]["items"]["minItems"] == 2
        assert schema["properties"]["items"]["maxItems"] == 5

    def test_the_tighter_minimum_wins_and_a_zero_maximum_is_carried(self) -> None:
        """``allow_empty=False`` beats ``min_length=0``; ``max_length=0`` is a bound, not
        silence, so it is read by ``is not None`` rather than truthiness."""
        spec = ServiceSpec(service=_service, input_serializer=_NonEmptyShortLine, many=True)
        schema = spec_to_json_schema(spec)
        assert schema is not None
        assert schema["properties"]["items"]["minItems"] == 1
        assert schema["properties"]["items"]["maxItems"] == 0

    def test_an_unconstrained_list_declares_no_bounds(self) -> None:
        spec = ServiceSpec(service=_service, input_serializer=_Line, many=True)
        schema = spec_to_json_schema(spec)
        assert schema is not None
        assert set(schema["properties"]["items"]) == {"type", "items"}

    def test_the_list_serializer_is_built_with_the_description_context(self) -> None:
        """The list serializer dispatch validates with is the one described, context
        included -- a list serializer reading ``context["request"]`` is described rather
        than raising ``KeyError``."""
        spec = ServiceSpec(service=_service, input_serializer=_LineReadingTheRequest, many=True)
        schema = spec_to_json_schema(spec)
        assert schema is not None
        assert schema["properties"]["items"]["items"] == _LINE_SCHEMA

    def test_max_depth_counts_from_the_item(self) -> None:
        """The wrapper is not a serializer level, so ``max_depth=1`` still publishes the
        item's own fields."""
        spec = ServiceSpec(service=_service, input_serializer=_NestedIO, many=True)
        schema = spec_to_json_schema(spec, max_depth=1)
        assert schema is not None
        assert schema["properties"]["items"]["items"] == {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "inner": {"type": "object"}},
            "required": ["id", "inner"],
        }

    def test_a_metadata_fragment_annotates_the_wrapper(self) -> None:
        spec = ServiceSpec(
            service=_service,
            input_serializer=_Line,
            many=True,
            metadata={"json_schema": {"input": {"description": "Add lines."}}},
        )
        schema = spec_to_json_schema(spec)
        assert schema is not None
        assert schema["description"] == "Add lines."
        assert schema["additionalProperties"] is False
