"""Tests for ``spec_to_json_schema``."""

from __future__ import annotations

import dataclasses
import json
from typing import Annotated, Any, Literal

import django_filters
import pytest
from django.core.exceptions import ImproperlyConfigured
from jsonschema import Draft202012Validator
from rest_framework import serializers
from typing_extensions import NotRequired, TypedDict, Unpack

from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.dispatch.null_progress import null_progress
from rest_framework_services.dispatch.render_spec_output import render_spec_output
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.jsonschema.spec_to_json_schema import spec_to_json_schema
from rest_framework_services.registry.capability_manifest import capability_manifest
from rest_framework_services.registry.spec_registry import SpecRegistry
from rest_framework_services.types.input_description import InputDescription
from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.not_client_input import NotClientInput
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


def test_an_allow_none_retrieve_admits_the_null_it_serves() -> None:
    """A miss is presented as ``None``, so the schema says ``null`` too.

    Read off the payload a miss actually renders and checked with a real
    validator: the schema said ``object`` alone, and every transport serving
    the miss served a value its own advertised schema refused.
    """
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        allow_none=True,
        selector=lambda: None,
        output_serializer=_Out,
    )
    result = dispatch_spec(spec, user=None, params={})
    payload = render_spec_output(spec, result.value)
    schema = spec_to_json_schema(spec, phase="output")

    assert payload is None
    assert schema == {
        "type": ["object", "null"],
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    }
    assert not list(Draft202012Validator(schema).iter_errors(payload))
    assert not list(Draft202012Validator(schema).iter_errors({"id": 1}))


def test_an_allow_none_list_is_still_an_array() -> None:
    """``allow_none`` is a RETRIEVE knob; a list never presents ``None``."""
    spec = SelectorSpec(kind=SelectorKind.LIST, allow_none=True, output_serializer=_Out)

    assert spec_to_json_schema(spec, phase="output") == {
        "type": "array",
        "items": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    }


def test_a_nested_output_selectors_allow_none_is_not_read() -> None:
    """Dispatch ignores ``allow_none`` on a ``ServiceSpec``'s output selector, so
    the schema does too."""
    spec = ServiceSpec(
        service=_service,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, allow_none=True, output_serializer=_Out
        ),
    )

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


# --- supplied= ---------------------------------------------------------------
#
# A transport knows statically which names it fills itself (its seeds, the
# registered pool seeds, ``kwargs=`` providers, URL kwargs). Passing them as
# ``supplied`` opts into the transport rule: a supplied name is not a client
# input, and an unsupplied parameter without a default is one the call cannot
# do without.


def _task_by_pk(user, *, pk: int, include_archived: bool = False): ...


def _outstanding(user, *, currency: str, status: str = "open"): ...


def _search(*, q: str = "", limit: int = 20): ...


def _by_slug(user, slug): ...


def _marked(*, limit: Annotated[int, InputRequired] = 20): ...


def _provider_owned(user, request, view, *, token: Annotated[str, NotClientInput]): ...


class _RouteExtras(TypedDict):  # total=True
    project_pk: int
    team: NotRequired[str]


def _nested(user, **extras: Unpack[_RouteExtras]): ...


class _MarkedExtras(TypedDict, total=False):
    project_pk: Annotated[int, InputRequired]


def _nested_marked(**extras: Unpack[_MarkedExtras]): ...


def _reporting(user, *, pk: int, progress, data): ...


def _positional(token, /, *, pk: int): ...


def _retrieve(selector: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector)


class TestSupplied:
    def test_a_lookup_without_a_default_is_required(self) -> None:
        # Advertised as optional before, while a call without it is refused.
        with pytest.raises(ServiceValidationError) as refused:
            dispatch_spec(_retrieve(_task_by_pk), user=None, params={})
        assert refused.value.detail == {"non_field_errors": ["Missing required argument(s): 'pk'."]}
        schema = spec_to_json_schema(_retrieve(_task_by_pk), supplied=frozenset())
        assert schema == {
            "type": "object",
            "properties": {"pk": {"type": "integer"}, "include_archived": {"type": "boolean"}},
            "required": ["pk"],
        }

    def test_an_unannotated_parameter_without_a_default_is_required(self) -> None:
        schema = spec_to_json_schema(_retrieve(_by_slug), supplied=frozenset())
        assert schema == {"type": "object", "properties": {"slug": {}}, "required": ["slug"]}

    def test_a_supplied_name_is_dropped_from_properties_and_required(self) -> None:
        schema = spec_to_json_schema(_retrieve(_outstanding), supplied=frozenset({"currency"}))
        assert schema == {"type": "object", "properties": {"status": {"type": "string"}}}

    def test_a_supplied_name_is_dropped_even_where_it_is_marked_required(self) -> None:
        schema = spec_to_json_schema(_retrieve(_marked), supplied=frozenset({"limit"}))
        assert schema == {"type": "object"}

    def test_a_defaulted_parameter_stays_optional(self) -> None:
        schema = spec_to_json_schema(_retrieve(_search), supplied=frozenset())
        assert schema == {
            "type": "object",
            "properties": {"q": {"type": "string"}, "limit": {"type": "integer"}},
        }

    @pytest.mark.parametrize("supplied", [None, frozenset()])
    def test_input_required_is_required_either_way(self, supplied: frozenset[str] | None) -> None:
        # ``limit`` has a default, so only the marker can be what requires it.
        schema = spec_to_json_schema(_retrieve(_marked), supplied=supplied)
        assert schema is not None
        assert schema["required"] == ["limit"]

    def test_not_client_input_and_transport_seeds_stay_unadvertised(self) -> None:
        # ``token`` has no default, yet stays out of both: a provider fills it.
        schema = spec_to_json_schema(_retrieve(_provider_owned), supplied=frozenset())
        assert schema == {"type": "object"}

    def test_reserved_pool_seeds_are_dropped_without_being_supplied(self) -> None:
        # ``base_pool`` always fills ``progress``, and ``data`` is never taken
        # from a selector's params, so a client can send neither. Neither has a
        # default, so listing them as required asked for what cannot be sent.
        schema = spec_to_json_schema(_retrieve(_reporting), supplied=frozenset())
        assert schema == {
            "type": "object",
            "properties": {"pk": {"type": "integer"}},
            "required": ["pk"],
        }

    def test_reserved_pool_seeds_reflect_as_before_without_supplied(self) -> None:
        assert json.dumps(spec_to_json_schema(_retrieve(_reporting))) == json.dumps(
            {
                "type": "object",
                "properties": {"pk": {"type": "integer"}, "progress": {}, "data": {}},
            }
        )

    def test_a_client_value_never_reaches_a_reserved_pool_seed(self) -> None:
        # What the two tests above rely on: the seed is filled over the caller's
        # value, and a selector's params never reach the others at all.
        seen: dict[str, Any] = {}

        def _status(*, pk: int, progress: Any, data: Any = None) -> dict[str, Any]:
            seen.update(progress=progress, data=data)
            return {"pk": pk}

        params = {"pk": 1, "progress": "client", "data": "client"}
        dispatch_spec(_retrieve(_status), user=None, params=params)
        assert seen == {"progress": null_progress, "data": None}

    def test_a_positional_only_parameter_is_reflected_but_never_inferred_required(
        self,
    ) -> None:
        # Dispatch binds a pool by keyword, so nothing a caller sends can fill
        # ``token``: requiring it would ask for what cannot be passed.
        schema = spec_to_json_schema(_retrieve(_positional), supplied=frozenset())
        assert schema == {
            "type": "object",
            "properties": {"token": {}, "pk": {"type": "integer"}},
            "required": ["pk"],
        }
        assert spec_to_json_schema(_retrieve(_positional)) == {
            "type": "object",
            "properties": {"token": {}, "pk": {"type": "integer"}},
        }

    def test_a_supplied_typed_dict_key_is_dropped(self) -> None:
        schema = spec_to_json_schema(_retrieve(_nested), supplied=frozenset({"project_pk"}))
        assert schema == {"type": "object", "properties": {"team": {"type": "string"}}}

    def test_a_typed_dict_key_keeps_its_declared_requiredness(self) -> None:
        # A key has no default to read: its ``TypedDict`` totality is the
        # declaration, so a required key stays required and a ``NotRequired``
        # one stays optional, exactly as without ``supplied``.
        schema = spec_to_json_schema(_retrieve(_nested), supplied=frozenset())
        assert schema == {
            "type": "object",
            "properties": {"project_pk": {"type": "integer"}, "team": {"type": "string"}},
            "required": ["project_pk"],
        }

    def test_a_supplied_input_required_typed_dict_key_is_dropped(self) -> None:
        schema = spec_to_json_schema(_retrieve(_nested_marked), supplied=frozenset({"project_pk"}))
        assert schema == {"type": "object"}

    def test_a_filter_set_field_is_caller_input_and_is_untouched(self) -> None:
        # The filter reads the caller's params, so a filter field of a supplied
        # name is still the caller's to send; only the parameter is dropped.
        class _FS(django_filters.FilterSet):
            currency = django_filters.CharFilter()

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_outstanding, filter_set=_FS)
        schema = spec_to_json_schema(spec, supplied=frozenset({"currency"}))
        assert schema == {
            "type": "object",
            "properties": {"status": {"type": "string"}, "currency": {"type": "string"}},
        }

    def test_a_service_spec_input_is_its_serializer_and_is_untouched(self) -> None:
        # The rule is about reflected callable parameters; a ServiceSpec's input
        # is its input serializer, whose fields are not reflected parameters.
        spec = ServiceSpec(service=_service, input_serializer=_Create)
        assert spec_to_json_schema(spec, supplied=frozenset({"name"})) == spec_to_json_schema(spec)

    def test_the_output_phase_reflects_no_parameters_and_is_untouched(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_task_by_pk, output_serializer=_Out
        )
        assert spec_to_json_schema(
            spec, phase="output", supplied=frozenset({"id"})
        ) == spec_to_json_schema(spec, phase="output")

    def test_none_is_the_default(self) -> None:
        spec = _retrieve(_task_by_pk)
        assert json.dumps(spec_to_json_schema(spec, supplied=None)) == json.dumps(
            spec_to_json_schema(spec)
        )


# --- the default is byte-identical to the reflection before ``supplied`` -------


class _UnchangedExtras(TypedDict, total=False):
    project_pk: Annotated[int, InputRequired, InputDescription("Owning project.")]
    team: str
    token: Annotated[str, NotClientInput]


class _UnchangedFilter(django_filters.FilterSet):
    name = django_filters.CharFilter()


def _unchanged_selector(
    user,
    request,
    pk: int,
    slug,
    *,
    status: Literal["open", "closed"] = "open",
    limit: Annotated[int, InputRequired] = 20,
    note: Annotated[str | None, InputDescription("Free text.")] = None,
    secret: Annotated[str, NotClientInput],
    **extras: Unpack[_UnchangedExtras],
): ...


_UNCHANGED_SPECS: dict[str, ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]] = {
    "retrieve": SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_unchanged_selector,
        output_serializer=_Out,
        allow_none=True,
    ),
    "list": SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_unchanged_selector,
        filter_set=_UnchangedFilter,
        output_serializer=_Out,
    ),
    "lookup": SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_task_by_pk),
    "nested": SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_nested),
    "create": ServiceSpec(
        service=_service,
        input_serializer=_Create,
        output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Out),
    ),
    "bulk": ServiceSpec(service=_service, input_serializer=_Create, many=True),
}

# Captured from the reflection as it stood before ``supplied`` existed. Compared
# as serialized text, so key order and ``required`` order count too: a dict
# literal keeps insertion order, so ``json.dumps`` of it is the old bytes.
_UNCHANGED_SCHEMAS: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] = {
    "bulk": (
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
                        "required": ["name"],
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
        None,
    ),
    "create": (
        {
            "type": "object",
            "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
            "required": ["name"],
        },
        {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    ),
    "list": (
        {
            "type": "object",
            "properties": {
                "pk": {"type": "integer"},
                "slug": {},
                "status": {"enum": ["open", "closed"]},
                "limit": {"type": "integer"},
                "note": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "Free text.",
                },
                "project_pk": {"type": "integer", "description": "Owning project."},
                "team": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["limit", "project_pk"],
        },
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
            },
        },
    ),
    "lookup": (
        {
            "type": "object",
            "properties": {"pk": {"type": "integer"}, "include_archived": {"type": "boolean"}},
        },
        None,
    ),
    "nested": (
        {
            "type": "object",
            "properties": {"project_pk": {"type": "integer"}, "team": {"type": "string"}},
            "required": ["project_pk"],
        },
        None,
    ),
    "retrieve": (
        {
            "type": "object",
            "properties": {
                "pk": {"type": "integer"},
                "slug": {},
                "status": {"enum": ["open", "closed"]},
                "limit": {"type": "integer"},
                "note": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "Free text.",
                },
                "project_pk": {"type": "integer", "description": "Owning project."},
                "team": {"type": "string"},
            },
            "required": ["limit", "project_pk"],
        },
        {"type": ["object", "null"], "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    ),
}


@pytest.mark.parametrize("name", sorted(_UNCHANGED_SPECS))
def test_the_default_reflection_is_byte_identical(name: str) -> None:
    spec = _UNCHANGED_SPECS[name]
    expected_input, expected_output = _UNCHANGED_SCHEMAS[name]
    assert json.dumps(spec_to_json_schema(spec)) == json.dumps(expected_input)
    assert json.dumps(spec_to_json_schema(spec, phase="output")) == json.dumps(expected_output)


def test_the_capability_manifest_publishes_the_same_schemas() -> None:
    # The one drfs-internal caller of ``spec_to_json_schema``. It passes no
    # ``supplied``, so it publishes exactly the schemas it published before.
    registry = SpecRegistry()
    for name, spec in _UNCHANGED_SPECS.items():
        registry.register(name, spec)
    published = {
        operation["name"]: (operation["input_schema"], operation["output_schema"])
        for operation in capability_manifest(registry)["operations"]
    }
    assert json.dumps(published, sort_keys=False) == json.dumps(
        {name: _UNCHANGED_SCHEMAS[name] for name in _UNCHANGED_SPECS}
    )
