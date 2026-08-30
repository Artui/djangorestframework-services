"""Tests for the internal JSON Schema walkers."""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import typing
import uuid
from decimal import Decimal
from typing import Any

from rest_framework import serializers
from typing_extensions import NotRequired, Required, TypedDict, Unpack

from rest_framework_services.jsonschema.utils import (
    _MAX_SERIALIZER_APPEARANCES,
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
        # Django prepends an empty choice whose label it words differently across
        # versions, so match the shape rather than the wording.
        assert [entry["const"] for entry in schema["oneOf"]][1:] == [str(tmp_path / "one.txt")]
        assert schema["oneOf"][0]["const"] == ""


def test_python_type_to_schema_maps_the_stdlib_scalars() -> None:
    # The same wire shapes ``_DRF_FIELD_TO_SCHEMA`` gives the matching DRF
    # fields, so one value described two ways does not describe two things.
    assert _python_type_to_schema(datetime.datetime) == {
        "type": "string",
        "format": "date-time",
    }
    assert _python_type_to_schema(datetime.date) == {"type": "string", "format": "date"}
    assert _python_type_to_schema(datetime.time) == {"type": "string", "format": "time"}
    assert _python_type_to_schema(uuid.UUID) == {"type": "string", "format": "uuid"}
    assert _python_type_to_schema(decimal.Decimal) == {"type": "string", "format": "decimal"}
    assert _python_type_to_schema(type(None)) == {"type": "null"}


def test_python_type_to_schema_publishes_a_literal_as_an_enum() -> None:
    assert _python_type_to_schema(typing.Literal["open", "closed"]) == {"enum": ["open", "closed"]}


def test_python_type_to_schema_publishes_an_enum_class_by_value() -> None:
    class _Status(enum.Enum):
        OPEN = "open"
        CLOSED = "closed"

    assert _python_type_to_schema(_Status) == {"enum": ["open", "closed"]}


def test_python_type_to_schema_widens_an_optional_to_any_of() -> None:
    assert _python_type_to_schema(str | None) == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert _python_type_to_schema(typing.Optional[int]) == {  # noqa: UP045 — the typing spelling is the point
        "anyOf": [{"type": "integer"}, {"type": "null"}]
    }
    assert _python_type_to_schema(typing.Union[int, str]) == {  # noqa: UP007 — ditto
        "anyOf": [{"type": "integer"}, {"type": "string"}]
    }


def test_python_type_to_schema_maps_sets_and_mappings() -> None:
    assert _python_type_to_schema(set[str]) == {
        "type": "array",
        "items": {"type": "string"},
        "uniqueItems": True,
    }
    assert _python_type_to_schema(frozenset) == {
        "type": "array",
        "items": {},
        "uniqueItems": True,
    }
    assert _python_type_to_schema(dict[str, int]) == {
        "type": "object",
        "additionalProperties": {"type": "integer"},
    }
    # An unconstrained value type adds nothing, so it is left off rather than
    # published as ``"additionalProperties": {}``.
    assert _python_type_to_schema(dict[str, typing.Any]) == {"type": "object"}
    assert _python_type_to_schema(dict) == {"type": "object"}
    assert _python_type_to_schema(list) == {"type": "array", "items": {}}


def test_python_type_to_schema_resolves_registry_rules_inside_containers() -> None:
    class _Money: ...

    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
        python_types=[(_Money, {"type": "string", "format": "money"})]
    )
    assert _python_type_to_schema(list[_Money], registry) == {
        "type": "array",
        "items": {"type": "string", "format": "money"},
    }
    assert _python_type_to_schema(_Money | None, registry) == {
        "anyOf": [{"type": "string", "format": "money"}, {"type": "null"}]
    }


def test_python_type_to_schema_still_falls_back_for_an_unregistered_class() -> None:
    class _Money: ...

    # ``{}`` is "any JSON value" — the documented escape hatch is a registry
    # rule, which is why the fallback stays permissive rather than raising.
    assert _python_type_to_schema(_Money) == {}
    assert _python_type_to_schema(typing.Any) == {}


class _Node(serializers.Serializer):
    """A self-referential declaration: a category tree, a threaded comment.

    Declared through ``get_fields`` because a class body cannot name the class
    it is still defining -- which is how these get written in the wild.
    """

    name = serializers.CharField()

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        fields["children"] = _Node(many=True)
        return fields


class _BoundedNode(serializers.Serializer):
    """The standard DRF answer to a recursive shape: a countdown per level.

    This declaration stops on its own -- the innermost instance declares no
    ``children`` at all -- so nothing here was ever at risk of recursing. It is
    the case a first-re-entry guard flattened to a single level.
    """

    name = serializers.CharField()

    def __init__(self, *args: Any, depth: int = 3, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if depth > 0:
            self.fields["children"] = _BoundedNode(depth=depth - 1, many=True)


def _nested(node: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any] | None:
    """The next serializer node down, unwrapping any array a ``many=True`` made.

    Takes several keys so a mutually recursive chain, which alternates the name
    it nests under, walks with the same helper as a self-referential one.
    """
    for key in keys:
        field = node["properties"].get(key)
        if field is not None:
            return field["items"] if field.get("type") == "array" else field
    return None


def _levels_described(schema: dict[str, Any], *keys: str) -> int:
    """How many links of a nesting chain carry properties of their own.

    Stops at the first node that publishes none -- a truncated ``{"type":
    "object"}``, or a leaf that declared no further nesting.
    """
    levels = 0
    node: dict[str, Any] = schema
    while "properties" in node:
        levels += 1
        nested = _nested(node, keys)
        if nested is None:
            break
        node = nested
    return levels


class _Author(serializers.Serializer):
    name = serializers.CharField()

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        fields["latest_post"] = _Post()
        return fields


class _Post(serializers.Serializer):
    title = serializers.CharField()

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        fields["author"] = _Author()
        return fields


class _Address(serializers.Serializer):
    line1 = serializers.CharField()


class _Order(serializers.Serializer):
    billing = _Address()
    shipping = _Address()


class _Level3(serializers.Serializer):
    value = serializers.IntegerField()


class _Level2(serializers.Serializer):
    level3 = _Level3()


class _Level1(serializers.Serializer):
    level2 = _Level2()


_TRUNCATED = {"type": "object"}


def _deepest(schema: dict[str, Any], *keys: str) -> dict[str, Any]:
    """The last node of a nesting chain that still publishes properties."""
    node: dict[str, Any] = schema
    while True:
        nested = _nested(node, keys)
        if nested is None or "properties" not in nested:
            return node
        node = nested


class TestCycleGuard:
    """A declaration must not be able to crash schema generation.

    Unguarded, the unbounded cases here are a ``RecursionError`` raised while a
    transport declares its tools -- before any request exists to fail. The guard
    is an *allowance* rather than a boolean because class identity cannot tell
    the unbounded form apart from one that bounds itself.
    """

    def test_a_self_referential_serializer_truncates_instead_of_crashing(self) -> None:
        schema = serializer_to_schema(_Node())

        assert schema["properties"]["name"] == {"type": "string"}
        assert _levels_described(schema, "children") == _MAX_SERIALIZER_APPEARANCES
        assert _deepest(schema, "children")["properties"]["children"] == {
            "type": "array",
            "items": _TRUNCATED,
        }

    def test_a_self_bounded_serializer_is_described_as_declared(self) -> None:
        """The countdown recipe terminates by itself and must not be flattened.

        Truncating at the first re-entry published one level where four were
        declared: not a lie -- a truncated node claims nothing -- but a caller
        reading the schema saw a flat object where the declaration has a tree.
        """
        schema = serializer_to_schema(_BoundedNode())

        # One level is what the first-re-entry guard published; four is what
        # ``_BoundedNode`` declares.
        assert _levels_described(schema, "children") == 4
        # The innermost level is the declaration's own leaf, not a truncation:
        # it publishes ``name`` and declares no ``children`` at all.
        assert _deepest(schema, "children") == {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }

    def test_a_self_bounded_serializer_deeper_than_the_allowance_still_truncates(self) -> None:
        """The allowance is a number, and a declaration may out-nest it."""
        schema = serializer_to_schema(_BoundedNode(depth=_MAX_SERIALIZER_APPEARANCES + 2))

        assert _levels_described(schema, "children") == _MAX_SERIALIZER_APPEARANCES
        assert _deepest(schema, "children")["properties"]["children"]["items"] == _TRUNCATED

    def test_a_mutually_recursive_pair_truncates(self) -> None:
        """Each class gets its own allowance: the path is counted per class."""
        schema = serializer_to_schema(_Author())

        post = schema["properties"]["latest_post"]
        assert post["properties"]["title"] == {"type": "string"}
        # _Author and _Post alternate, and each is counted only against its own
        # appearances, so the pair spends twice the allowance before truncating.
        assert _levels_described(schema, "latest_post", "author") == 2 * _MAX_SERIALIZER_APPEARANCES
        assert _nested(_deepest(schema, "latest_post", "author"), ("latest_post", "author")) == (
            _TRUNCATED
        )

    def test_the_same_serializer_on_sibling_branches_is_described_in_full(self) -> None:
        """The guard is a *path*, not a seen-set: two branches are not a cycle."""
        schema = serializer_to_schema(_Order())

        described = {"type": "object", "properties": {"line1": {"type": "string"}}}
        assert schema["properties"]["billing"] == {**described, "required": ["line1"]}
        assert schema["properties"]["shipping"] == {**described, "required": ["line1"]}

    def test_a_truncated_node_keeps_what_its_parent_declared_about_it(self) -> None:
        """``title`` / ``description`` come from the *parent's* field, not the walk."""

        class _Documented(serializers.Serializer):
            name = serializers.CharField()

            def get_fields(self) -> dict[str, serializers.Field]:
                fields = super().get_fields()
                fields["children"] = _Documented(many=True, help_text="the child nodes")
                return fields

        schema = serializer_to_schema(_Documented())

        assert _deepest(schema, "children")["properties"]["children"] == {
            "type": "array",
            "items": _TRUNCATED,
            "description": "the child nodes",
        }

    def test_the_guard_survives_the_output_direction(self) -> None:
        schema = serializer_to_schema(_Node(), for_output=True)

        assert _levels_described(schema, "children") == _MAX_SERIALIZER_APPEARANCES
        assert _deepest(schema, "children")["properties"]["children"] == {
            "type": "array",
            "items": _TRUNCATED,
        }


class TestMaxDepth:
    """An opt-in size bound. Unset means what it has always meant."""

    def test_unset_describes_every_level(self) -> None:
        schema = serializer_to_schema(_Level1())

        assert schema["properties"]["level2"]["properties"]["level3"] == {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        }

    def test_the_root_is_level_one_so_one_truncates_every_nested_serializer(self) -> None:
        schema = serializer_to_schema(_Level1(), max_depth=1)

        assert schema == {
            "type": "object",
            "properties": {"level2": _TRUNCATED},
            "required": ["level2"],
        }

    def test_the_bound_lets_through_exactly_as_many_levels_as_it_names(self) -> None:
        schema = serializer_to_schema(_Level1(), max_depth=2)

        level2 = schema["properties"]["level2"]
        assert level2["properties"]["level3"] == _TRUNCATED
        assert level2["required"] == ["level3"]

    def test_below_one_truncates_the_root_itself(self) -> None:
        assert serializer_to_schema(_Level1(), max_depth=0) == _TRUNCATED

    def test_an_array_wrapper_costs_no_level(self) -> None:
        """``many=True`` is this walker's own shape, not a serializer level."""

        class _Many(serializers.Serializer):
            rows = _Level2(many=True)

        schema = serializer_to_schema(_Many(), max_depth=2)

        assert schema["properties"]["rows"]["items"]["properties"]["level3"] == _TRUNCATED

    def test_the_bound_reaches_a_list_field_child(self) -> None:
        class _Listed(serializers.Serializer):
            rows = serializers.ListField(child=_Level2())

        schema = serializer_to_schema(_Listed(), max_depth=2)

        assert schema["properties"]["rows"]["items"]["properties"]["level3"] == _TRUNCATED

    def test_it_wins_over_the_re_entry_allowance_when_it_is_tighter(self) -> None:
        """A caller asking for two levels gets two, allowance or no allowance.

        The allowance is a floor under what generation does when nobody asked
        for a bound -- never a quota the caller has to spend. Both cases here
        would be described far deeper if the allowance alone decided: the
        unbounded one to the allowance itself, the self-bounded one to the four
        levels it declares.
        """
        assert _MAX_SERIALIZER_APPEARANCES > 2

        for serializer in (_Node(), _BoundedNode()):
            schema = serializer_to_schema(serializer, max_depth=2)

            assert _levels_described(schema, "children") == 2
            assert _deepest(schema, "children")["properties"]["children"]["items"] == _TRUNCATED

    def test_the_allowance_wins_when_max_depth_is_the_looser_of_the_two(self) -> None:
        """The other side of the same ``or``: a generous ceiling cedes to it."""
        schema = serializer_to_schema(_Node(), max_depth=_MAX_SERIALIZER_APPEARANCES + 3)

        assert _levels_described(schema, "children") == _MAX_SERIALIZER_APPEARANCES
