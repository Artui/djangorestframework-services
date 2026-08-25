"""Tests for the internal JSON Schema walkers."""

from __future__ import annotations

import dataclasses
import typing
from decimal import Decimal
from typing import Any

from rest_framework import serializers
from typing_extensions import NotRequired, Required, TypedDict, Unpack

from rest_framework_services.jsonschema.utils import (
    _python_type_to_schema,
    apply_field_override,
    apply_serializer_overrides,
    callable_input_schema,
    dataclass_to_schema,
    field_to_schema,
    serializer_to_schema,
)
from rest_framework_services.types.json_schema_registry import DEFAULT_JSON_SCHEMA_REGISTRY


class _MoneyField(serializers.Field): ...


class _Inner(serializers.Serializer):
    x = serializers.IntegerField()


class _Sample(serializers.Serializer):
    name = serializers.CharField(help_text="the name")
    count = serializers.IntegerField(required=False)
    ro = serializers.CharField(read_only=True)
    tags = serializers.ListField(child=serializers.CharField())
    nested = _Inner()
    choice = serializers.ChoiceField(choices=["a", "b"])


# --- field_to_schema / _field_to_schema_default ------------------------------


def test_scalar_fields_map_to_json_schema() -> None:
    assert field_to_schema(serializers.BooleanField()) == {"type": "boolean"}
    assert field_to_schema(serializers.IntegerField()) == {"type": "integer"}
    assert field_to_schema(serializers.DateTimeField()) == {
        "type": "string",
        "format": "date-time",
    }
    assert field_to_schema(serializers.CharField()) == {"type": "string"}


def test_unknown_field_type_falls_back_to_any() -> None:
    class WeirdField(serializers.Field): ...

    assert field_to_schema(WeirdField()) == {}


def test_choice_field_becomes_enum() -> None:
    assert field_to_schema(serializers.ChoiceField(choices=["a", "b"])) == {"enum": ["a", "b"]}


def test_list_field_recurses_into_child() -> None:
    schema = field_to_schema(serializers.ListField(child=serializers.IntegerField()))
    assert schema == {"type": "array", "items": {"type": "integer"}}


def test_list_field_with_no_child_is_array_of_any() -> None:
    lf = serializers.ListField(child=serializers.CharField())
    lf.child = None  # exercise the defensive None-child guard
    assert field_to_schema(lf) == {"type": "array", "items": {}}


def test_list_serializer_recurses_into_child_serializer() -> None:
    schema = field_to_schema(_Inner(many=True))
    assert schema == {
        "type": "array",
        "items": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
    }


def test_list_serializer_with_no_child_is_array_of_any() -> None:
    ls = serializers.ListSerializer(child=serializers.Serializer())
    ls.child = None  # exercise the defensive None-child guard
    assert field_to_schema(ls) == {"type": "array", "items": {}}


# --- serializer_to_schema ----------------------------------------------------


def test_serializer_to_schema_honours_required_help_text_and_read_only() -> None:
    schema = serializer_to_schema(_Sample())
    assert schema["type"] == "object"
    props = schema["properties"]
    assert "ro" not in props  # read_only fields are skipped
    assert props["name"] == {"type": "string", "description": "the name"}
    assert props["count"] == {"type": "integer"}
    assert props["choice"] == {"enum": ["a", "b"]}
    assert props["tags"] == {"type": "array", "items": {"type": "string"}}
    assert props["nested"] == {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
    }
    # ``count`` is optional; everything else required.
    assert schema["required"] == ["name", "tags", "nested", "choice"]


def test_serializer_with_no_required_fields_omits_required() -> None:
    class _Opt(serializers.Serializer):
        a = serializers.IntegerField(required=False)

    assert serializer_to_schema(_Opt()) == {
        "type": "object",
        "properties": {"a": {"type": "integer"}},
    }


# --- _python_type_to_schema / dataclass_to_schema ----------------------------


def test_python_type_to_schema_scalars_list_and_fallback() -> None:
    assert _python_type_to_schema(str) == {"type": "string"}
    assert _python_type_to_schema(int) == {"type": "integer"}
    assert _python_type_to_schema(float) == {"type": "number"}
    assert _python_type_to_schema(bool) == {"type": "boolean"}
    assert _python_type_to_schema(list[int]) == {"type": "array", "items": {"type": "integer"}}
    # Unparametrised list generic (origin ``list``, no args) → array of any,
    # exercising the ``or (Any,)`` fallback. The attribute name is held in a
    # variable so ruff neither rewrites a ``typing.List`` literal to ``list``
    # (UP, which would erase the ``list`` origin) nor flags a constant getattr (B009).
    unsubscripted = "List"
    assert _python_type_to_schema(getattr(typing, unsubscripted)) == {"type": "array", "items": {}}
    assert _python_type_to_schema(bytes) == {}


def test_dataclass_to_schema_marks_required_vs_defaulted() -> None:
    @dataclasses.dataclass
    class _DC:
        a: str
        b: int = 3
        c: list[str] = dataclasses.field(default_factory=list)

    assert dataclass_to_schema(_DC) == {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "integer"},
            "c": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["a"],
    }


def test_dataclass_with_all_defaults_omits_required() -> None:
    @dataclasses.dataclass
    class _DC:
        a: int = 0

    assert dataclass_to_schema(_DC) == {"type": "object", "properties": {"a": {"type": "integer"}}}


# --- apply_serializer_overrides ---------------------------------------------


def _annotated(**annotation: Any) -> type:
    class _C:
        _spectacular_annotation = annotation

    return _C


def test_serializer_overrides_no_op_without_annotation() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert apply_serializer_overrides(dict(schema), object) == schema


def test_serializer_overrides_skips_field_level_annotation() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    cls = _annotated(field={"type": "string", "format": "iban"})
    assert apply_serializer_overrides(dict(schema), cls) == schema


def test_serializer_overrides_exclude_deprecate_and_examples() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    }
    cls = _annotated(
        exclude_fields=["a", "missing"],
        deprecate_fields=["b", "gone"],
        examples=[
            type("Ex", (), {"value": {"b": 1}})(),
            type("Ex", (), {"value": None})(),  # filtered
        ],
    )
    result = apply_serializer_overrides(dict(schema), cls)
    assert "a" not in result["properties"]
    assert result["required"] == ["b"]
    assert result["properties"]["b"]["deprecated"] is True
    assert result["examples"] == [{"b": 1}]


def test_serializer_overrides_drop_empty_required_and_no_examples() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    cls = _annotated(exclude_fields=["a"], examples=[])
    result = apply_serializer_overrides(dict(schema), cls)
    assert "required" not in result
    assert "examples" not in result


# --- apply_field_override -----------------------------------------------------


def test_field_override_replaces_with_dict_form() -> None:
    field = serializers.CharField()
    field._spectacular_annotation = {"field": {"type": "string", "format": "iban"}}
    assert apply_field_override(field, {"type": "string"}) == {"type": "string", "format": "iban"}


def test_field_override_ignores_non_dict_form() -> None:
    field = serializers.CharField()
    field._spectacular_annotation = {"field": str}  # a typeref, not a dict
    assert apply_field_override(field, {"type": "string"}) == {"type": "string"}


def test_field_override_no_op_without_annotation() -> None:
    assert apply_field_override(serializers.CharField(), {"type": "string"}) == {"type": "string"}


# --- registry rules (fields / python types) ----------------------------------


def test_registry_field_rule_maps_a_custom_field() -> None:
    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
        fields=[(_MoneyField, {"type": "string", "format": "money"})]
    )
    assert field_to_schema(_MoneyField(), registry) == {"type": "string", "format": "money"}


def test_registry_field_rule_present_but_unmatched_falls_through() -> None:
    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(fields=[(_MoneyField, {"x": 1})])
    assert field_to_schema(serializers.CharField(), registry) == {"type": "string"}


def test_registry_field_rule_overrides_a_builtin() -> None:
    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
        fields=[(serializers.CharField, {"type": "string", "format": "slug"})]
    )
    assert field_to_schema(serializers.CharField(), registry) == {
        "type": "string",
        "format": "slug",
    }


def test_registry_field_rule_does_not_mutate_the_registered_schema() -> None:
    fragment = {"type": "string"}
    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(fields=[(_MoneyField, fragment)])

    class _S(serializers.Serializer):
        amount = _MoneyField(help_text="how much")

    serializer_to_schema(_S(), registry)
    # ``description`` was added to the per-call copy, not the registered dict.
    assert fragment == {"type": "string"}


def test_registry_python_type_rule_maps_a_custom_type() -> None:
    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
        python_types=[(Decimal, {"type": "string", "format": "decimal"})]
    )
    assert _python_type_to_schema(Decimal, registry) == {"type": "string", "format": "decimal"}


def test_registry_python_type_rule_present_but_unmatched_falls_through() -> None:
    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(python_types=[(Decimal, {"x": 1})])
    assert _python_type_to_schema(str, registry) == {"type": "string"}


def test_registry_python_type_rule_flows_through_dataclass_walk() -> None:
    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
        python_types=[(Decimal, {"type": "string", "format": "decimal"})]
    )

    @dataclasses.dataclass
    class _DC:
        price: Decimal

    assert dataclass_to_schema(_DC, registry) == {
        "type": "object",
        "properties": {"price": {"type": "string", "format": "decimal"}},
        "required": ["price"],
    }


# --- callable_input_schema ---------------------------------------------------


def test_callable_input_schema_maps_annotations_and_skips_names() -> None:
    def selector(user, request, pk: int, name: str): ...

    props, required = callable_input_schema(selector, skip=frozenset({"user", "request"}))
    assert props == {"pk": {"type": "integer"}, "name": {"type": "string"}}
    assert required == []


def test_callable_input_schema_surfaces_unannotated_param_untyped() -> None:
    def selector(pk): ...

    assert callable_input_schema(selector) == ({"pk": {}}, [])


def test_callable_input_schema_skips_var_positional_and_bare_var_keyword() -> None:
    def selector(pk: int, *args, **kwargs): ...

    assert callable_input_schema(selector) == ({"pk": {"type": "integer"}}, [])


def test_callable_input_schema_untyped_on_unresolvable_annotation() -> None:
    def selector(pk: Ghost): ...  # noqa: F821 — deliberately unresolvable forward ref

    assert callable_input_schema(selector) == ({"pk": {}}, [])


class _ChildExtras(TypedDict, total=False):
    parent_pk: int
    label: str


def test_callable_input_schema_expands_unpack_typed_dict_optional_keys() -> None:
    def selector(user, **extras: Unpack[_ChildExtras]): ...

    props, required = callable_input_schema(selector, skip=frozenset({"user"}))
    assert props == {"parent_pk": {"type": "integer"}, "label": {"type": "string"}}
    assert required == []  # total=False → every key optional


class _ScopedExtras(TypedDict):
    project_pk: int  # required (total=True)
    note: NotRequired[str]  # opted out, even under PEP 563 stringization
    user: Required[int]  # required but skipped as a reserved seed


def test_callable_input_schema_unpack_required_keys_and_reserved_exclusion() -> None:
    def selector(**extras: Unpack[_ScopedExtras]): ...

    props, required = callable_input_schema(selector, skip=frozenset({"user", "request"}))
    assert props == {"project_pk": {"type": "integer"}, "note": {"type": "string"}}
    # ``project_pk`` is required; ``note`` is NotRequired; ``user`` is skipped
    # entirely (reserved) so it never appears in properties *or* required.
    assert required == ["project_pk"]


def test_callable_input_schema_bare_var_keyword_reflects_nothing() -> None:
    def selector(pk: int, **extras: Any): ...

    props, required = callable_input_schema(selector)
    assert props == {"pk": {"type": "integer"}}
    assert required == []


class _OutputInner(serializers.Serializer):
    ro_inner = serializers.CharField(read_only=True)
    wo_inner = serializers.CharField(write_only=True)


class _OutputSample(serializers.Serializer):
    name = serializers.CharField()
    generated = serializers.CharField(read_only=True)
    secret = serializers.CharField(write_only=True)
    nested = _OutputInner()
    tags = serializers.ListField(child=_OutputInner())


class TestOutputDirection:
    """``for_output=True`` describes what DRF renders, not what it accepts."""

    def test_read_only_survives_and_write_only_drops(self) -> None:
        schema = serializer_to_schema(_OutputSample(), for_output=True)

        assert "generated" in schema["properties"]
        assert "secret" not in schema["properties"]

    def test_required_claims_only_the_keys_drf_cannot_omit(self) -> None:
        """``Field.get_attribute`` raises ``SkipField`` and the key never appears.

        ``generated`` is ``read_only``, so it is rendered but not *guaranteed*:
        with no default and no ``allow_null`` it vanishes when the source
        attribute is missing, which is ordinary for a dict-sourced output.
        """
        schema = serializer_to_schema(_OutputSample(), for_output=True)

        assert schema["required"] == ["name", "nested", "tags"]
        assert "generated" in schema["properties"]

    def test_a_default_or_allow_null_makes_a_key_guaranteed(self) -> None:
        class _Guaranteed(serializers.Serializer):
            defaulted = serializers.CharField(required=False, default="x")
            nullable = serializers.CharField(required=False, allow_null=True)
            skippable = serializers.CharField(required=False)

        schema = serializer_to_schema(_Guaranteed(), for_output=True)

        assert schema["required"] == ["defaulted", "nullable"]

    def test_input_direction_is_unchanged(self) -> None:
        schema = serializer_to_schema(_OutputSample())

        assert "generated" not in schema["properties"]
        assert "secret" in schema["properties"]

    def test_direction_reaches_nested_serializers(self) -> None:
        schema = serializer_to_schema(_OutputSample(), for_output=True)

        assert "ro_inner" in schema["properties"]["nested"]["properties"]
        assert "wo_inner" not in schema["properties"]["nested"]["properties"]

    def test_direction_reaches_list_children(self) -> None:
        schema = serializer_to_schema(_OutputSample(), for_output=True)

        assert "ro_inner" in schema["properties"]["tags"]["items"]["properties"]


class TestChoiceSchema:
    def test_labels_that_repeat_their_value_stay_a_bare_enum(self) -> None:
        field = serializers.ChoiceField(choices=["a", "b"])

        assert field_to_schema(field, DEFAULT_JSON_SCHEMA_REGISTRY) == {"enum": ["a", "b"]}

    def test_distinct_labels_become_oneof_with_titles(self) -> None:
        field = serializers.ChoiceField(choices=[("P", "Pending"), ("D", "Done")])

        assert field_to_schema(field, DEFAULT_JSON_SCHEMA_REGISTRY) == {
            "oneOf": [{"const": "P", "title": "Pending"}, {"const": "D", "title": "Done"}]
        }

    def test_allow_blank_and_allow_null_widen_the_enum(self) -> None:
        field = serializers.ChoiceField(choices=["a"], allow_blank=True, allow_null=True)

        assert field_to_schema(field, DEFAULT_JSON_SCHEMA_REGISTRY) == {"enum": ["a", "", None]}

    def test_allow_blank_and_allow_null_widen_the_oneof(self) -> None:
        field = serializers.ChoiceField(
            choices=[("P", "Pending")], allow_blank=True, allow_null=True
        )

        assert field_to_schema(field, DEFAULT_JSON_SCHEMA_REGISTRY) == {
            "oneOf": [{"const": "P", "title": "Pending"}, {"const": ""}, {"const": None}]
        }


class TestMultipleChoiceField:
    """It subclasses ``ChoiceField`` but accepts a *set*, so it needs an array."""

    def test_becomes_a_unique_array_of_the_choices(self) -> None:
        field = serializers.MultipleChoiceField(choices=[("P", "Pending")])

        assert field_to_schema(field, DEFAULT_JSON_SCHEMA_REGISTRY) == {
            "type": "array",
            "items": {"oneOf": [{"const": "P", "title": "Pending"}]},
            "uniqueItems": True,
        }

    def test_disallowing_empty_sets_min_items(self) -> None:
        field = serializers.MultipleChoiceField(choices=["a"], allow_empty=False)

        assert field_to_schema(field, DEFAULT_JSON_SCHEMA_REGISTRY)["minItems"] == 1

    def test_member_schema_is_not_widened_by_the_field_s_own_nullability(self) -> None:
        """``allow_null`` is about the array, not about a member value."""
        field = serializers.MultipleChoiceField(choices=["a"], allow_null=True)

        assert field_to_schema(field, DEFAULT_JSON_SCHEMA_REGISTRY)["items"] == {"enum": ["a"]}

    def test_file_path_field_keeps_the_single_valued_branch(self, tmp_path: Any) -> None:
        """It subclasses ``ChoiceField`` too, but picks one path, not a set."""
        (tmp_path / "one.txt").write_text("")
        field = serializers.FilePathField(path=str(tmp_path))
        schema = field_to_schema(field, DEFAULT_JSON_SCHEMA_REGISTRY)

        assert schema.get("type") != "array"
        assert [entry["title"] for entry in schema["oneOf"]] == ["---------", "one.txt"]
