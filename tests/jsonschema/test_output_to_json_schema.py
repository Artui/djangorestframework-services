"""Tests for ``output_to_json_schema``."""

from __future__ import annotations

import dataclasses

import pytest
from rest_framework import serializers

from rest_framework_services.dispatch.render_spec_output import render_spec_output
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec


class _Out(serializers.Serializer):
    id = serializers.IntegerField()


@dataclasses.dataclass
class _OutDC:
    id: int


_ITEM = {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}


def test_none_serializer_yields_none() -> None:
    assert output_to_json_schema(None) is None


class _Plain: ...


@pytest.mark.parametrize(
    "declared", [_Plain, _Out(many=True)], ids=["plain-class", "serializer-instance"]
)
def test_an_output_that_is_neither_a_serializer_nor_a_dataclass_is_refused(
    declared: object,
) -> None:
    """Refused rather than answered with ``None``, which means "no output declared".

    ``render_spec_output`` instantiates the declaration as a serializer, so both
    of these fail at the first call. A ``None`` here described a tool with no
    output that then raised when it had one to render - the disagreement the
    input side stopped having when it began refusing the same inputs.
    """
    with pytest.raises(TypeError, match="Cannot derive an output JSON Schema") as refused:
        output_to_json_schema(declared)  # ty: ignore[invalid-argument-type]
    assert "BaseSerializer subclass" in str(refused.value)


class _Shout(serializers.BaseSerializer):
    """DRF's documented read-only pattern: a BaseSerializer with no declared fields."""

    def to_representation(self, instance: dict[str, str]) -> dict[str, str]:
        return {"shout": instance["word"].upper()}


def test_a_base_serializer_subclass_renders_so_it_gets_no_schema_rather_than_a_refusal() -> None:
    """Neither a ``Serializer`` nor a dataclass, and still a working output.

    It renders through ``render_spec_output`` like any serializer, so refusing it
    would break a spec that works; it declares no fields, so ``None`` is the
    honest schema. The refusal is for declarations that cannot render at all.
    """
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: None, output_serializer=_Shout)
    assert render_spec_output(spec, {"word": "hi"}, many=False) == {"shout": "HI"}
    assert output_to_json_schema(_Shout) is None


def test_an_undeclared_output_is_still_none_not_a_refusal() -> None:
    # The refusal must not swallow the one case where None is the true answer.
    assert output_to_json_schema(None, kind=SelectorKind.LIST, paginate=True) is None


def test_retrieve_or_default_kind_is_bare_item() -> None:
    assert output_to_json_schema(_Out) == _ITEM
    assert output_to_json_schema(_Out, kind=SelectorKind.RETRIEVE) == _ITEM


def test_dataclass_serializer_is_walked() -> None:
    assert output_to_json_schema(_OutDC) == _ITEM


def test_list_without_pagination_is_array() -> None:
    assert output_to_json_schema(_Out, kind=SelectorKind.LIST) == {
        "type": "array",
        "items": _ITEM,
    }


def test_list_with_pagination_is_envelope() -> None:
    assert output_to_json_schema(_Out, kind=SelectorKind.LIST, paginate=True) == {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": _ITEM},
            "page": {"type": "integer"},
            "totalPages": {"type": "integer"},
            "hasNext": {"type": "boolean"},
        },
        "required": ["items", "page", "totalPages", "hasNext"],
    }


def test_output_serializer_reading_request_context_is_described() -> None:
    """The output half of the same gate -- see the input-side test."""

    class _ContextAware(serializers.Serializer):
        def get_fields(self) -> dict[str, serializers.Field]:
            fields = super().get_fields()
            assert self.context["request"] is None
            fields["title"] = serializers.CharField()
            return fields

    schema = output_to_json_schema(_ContextAware, kind=SelectorKind.RETRIEVE)

    assert schema is not None
    assert schema["properties"]["title"] == {"type": "string"}


class _NestedOut(serializers.Serializer):
    id = serializers.IntegerField()
    inner = _Out()


class TestMaxDepth:
    def test_unset_describes_the_nested_serializer(self) -> None:
        schema = output_to_json_schema(_NestedOut)

        assert schema is not None
        assert schema["properties"]["inner"] == _ITEM

    def test_the_bound_lands_on_the_item_inside_the_envelope(self) -> None:
        """The envelope and the array are this function's shapes, not a level."""
        schema = output_to_json_schema(
            _NestedOut, kind=SelectorKind.LIST, paginate=True, max_depth=1
        )

        assert schema is not None
        item = schema["properties"]["items"]["items"]
        assert item["properties"]["inner"] == {"type": "object"}
        assert item["properties"]["id"] == {"type": "integer"}
