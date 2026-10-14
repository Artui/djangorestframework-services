"""Tests for the serializer field walk in ``service_specs/utils.py``."""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy
from django_service_specs.parameters.parameters import Parameter, Parameters
from django_service_specs.types.unset import UNSET as KERNEL_UNSET
from rest_framework import serializers
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services.service_specs.utils import describe_serializer, input_parameters
from rest_framework_services.types.unset import UNSET
from tests.testapp.models import Post, Tag


def _walk(declaration: Any) -> Parameters:
    return input_parameters(describe_serializer(declaration))


def _one(field: serializers.Field) -> Parameter:
    """The Parameter one field declares, as ``x`` on a serializer of its own."""
    cls = type("_One", (serializers.Serializer,), {"x": field})
    found = _walk(cls).get("x")
    assert found is not None
    return found


class _Custom(serializers.Field):
    def to_internal_value(self, data: Any) -> Any:
        return data


class _Leaf(serializers.Serializer):
    x = serializers.CharField()


# --- describe_serializer ----------------------------------------------------------


def test_a_serializer_class_is_described_as_an_instance_of_itself() -> None:
    class S(serializers.Serializer):
        name = serializers.CharField()

    described = describe_serializer(S)
    assert type(described) is S
    # The context DRF builds on HTTP, with no view or request to describe for.
    assert described.context == {"request": None, "format": None, "view": None}


def test_a_dataclass_is_described_through_a_dataclass_serializer() -> None:
    @dataclasses.dataclass
    class D:
        name: str

    described = describe_serializer(D)
    assert isinstance(described, DataclassSerializer)
    assert list(described.fields) == ["name"]


@pytest.mark.parametrize(
    "declaration",
    [
        pytest.param(serializers.Serializer(), id="an-instance"),
        pytest.param(dict, id="another-class"),
        pytest.param(None, id="none"),
    ],
)
def test_anything_but_a_serializer_class_or_a_dataclass_is_refused(declaration: Any) -> None:
    with pytest.raises(ImproperlyConfigured, match="Serializer subclass or a dataclass"):
        describe_serializer(declaration)


# --- one field --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "json_type", "fmt"),
    [
        pytest.param(serializers.BooleanField(), "boolean", None, id="boolean"),
        pytest.param(serializers.IntegerField(), "integer", None, id="integer"),
        pytest.param(serializers.FloatField(), "number", None, id="float"),
        pytest.param(
            serializers.DecimalField(max_digits=5, decimal_places=2),
            "string",
            "decimal",
            id="decimal",
        ),
        pytest.param(serializers.DateTimeField(), "string", "date-time", id="date-time"),
        pytest.param(serializers.DateField(), "string", "date", id="date"),
        pytest.param(serializers.TimeField(), "string", None, id="time"),
        pytest.param(serializers.DurationField(), "string", None, id="duration"),
        pytest.param(serializers.UUIDField(), "string", None, id="uuid"),
        pytest.param(serializers.EmailField(), "string", None, id="email"),
        pytest.param(serializers.URLField(), "string", None, id="url"),
        pytest.param(serializers.SlugField(), "string", None, id="slug"),
        pytest.param(serializers.IPAddressField(), "string", None, id="ip-address"),
        pytest.param(serializers.RegexField(r"^a"), "string", None, id="regex"),
        pytest.param(serializers.CharField(), "string", None, id="char"),
        pytest.param(serializers.DictField(), "object", None, id="dict"),
        pytest.param(serializers.HStoreField(), "object", None, id="hstore"),
        pytest.param(serializers.JSONField(), "any", None, id="json"),
        pytest.param(
            serializers.ModelField(Post._meta.get_field("title")), "any", None, id="model"
        ),
        pytest.param(_Custom(), "any", None, id="custom"),
        pytest.param(serializers.FileField(), "file", None, id="file"),
        pytest.param(serializers.ImageField(), "file", None, id="image"),
        pytest.param(
            serializers.PrimaryKeyRelatedField(queryset=Tag.objects.all()),
            "any",
            None,
            id="primary-key",
        ),
        pytest.param(
            serializers.SlugRelatedField(slug_field="name", queryset=Tag.objects.all()),
            "any",
            None,
            id="slug-related",
        ),
    ],
)
def test_each_field_declares_its_json_type_and_format(
    field: serializers.Field, json_type: str, fmt: str | None
) -> None:
    found = _one(field)
    assert (found.type, found.format) == (json_type, fmt)


def test_an_open_mapping_declares_no_fields_so_any_key_is_taken() -> None:
    assert _one(serializers.DictField()).fields is None


def test_a_required_field_is_required_and_takes_no_null() -> None:
    found = _one(serializers.CharField())
    assert (found.required, found.nullable, found.default) == (True, False, KERNEL_UNSET)


def test_allow_null_is_nullable() -> None:
    assert _one(serializers.CharField(allow_null=True)).nullable is True


def test_a_literal_default_is_reported_and_the_field_is_optional() -> None:
    found = _one(serializers.IntegerField(default=3))
    assert (found.required, found.default) == (False, 3)


def test_a_default_of_none_is_a_real_default() -> None:
    found = _one(serializers.CharField(default=None, allow_null=True))
    assert found.default is None


@pytest.mark.parametrize(
    "default",
    [
        pytest.param(lambda: 3, id="a-function"),
        pytest.param(serializers.CreateOnlyDefault(3), id="a-callable-instance"),
    ],
)
def test_a_callable_default_is_no_literal_default(default: Any) -> None:
    found = _one(serializers.IntegerField(default=default))
    assert (found.required, found.default) == (False, KERNEL_UNSET)


def test_the_unset_sentinel_as_a_default_is_no_default() -> None:
    assert _one(serializers.CharField(default=UNSET)).default is KERNEL_UNSET


def test_help_text_is_help_rendered_as_a_string() -> None:
    found = _one(serializers.CharField(help_text=gettext_lazy("The title.")))
    assert found.help == "The title."
    assert type(found.help) is str


def test_a_field_with_no_help_text_has_no_help() -> None:
    assert _one(serializers.CharField()).help is None


# --- which fields are parameters ------------------------------------------------


def test_a_hidden_field_and_every_read_only_field_are_not_parameters() -> None:
    class S(serializers.Serializer):
        name = serializers.CharField()
        secret = serializers.CharField(write_only=True)
        who = serializers.HiddenField(default=serializers.CurrentUserDefault())
        computed = serializers.SerializerMethodField()
        shown = serializers.ReadOnlyField()
        fixed = serializers.CharField(read_only=True)

        def get_computed(self, obj: Any) -> int:
            return 1

    assert [p.name for p in _walk(S)] == ["name", "secret"]


def test_a_parameter_is_named_for_the_wire_not_the_source() -> None:
    class S(serializers.Serializer):
        headline = serializers.CharField(source="title")

    assert [p.name for p in _walk(S)] == ["headline"]


def test_a_model_serializer_declares_its_writable_fields_in_order() -> None:
    class S(serializers.ModelSerializer):
        class Meta:
            model = Post
            fields = ("id", "title", "body", "published", "views", "author", "tags")

    params = _walk(S)
    assert [p.name for p in params] == ["title", "body", "published", "views", "author", "tags"]
    title, body = params.get("title"), params.get("body")
    assert title is not None and body is not None
    assert (title.type, title.required) == ("string", True)
    # A model default is not the serializer field's default: ModelSerializer
    # makes the field optional and leaves the default to the model.
    assert (body.required, body.default) == (False, KERNEL_UNSET)
    author, tags = params.get("author"), params.get("tags")
    assert author is not None and tags is not None
    assert (author.type, author.nullable) == ("any", True)
    assert (tags.type, tags.items) == ("array", None)


# --- choices ----------------------------------------------------------------------


def test_choices_carry_their_shared_type_and_labels() -> None:
    found = _one(serializers.ChoiceField(choices=[(1, "One"), (2, "Two")]))
    assert found.type == "integer"
    assert found.choices == (1, 2)
    assert found.choice_labels == ((1, "One"), (2, "Two"))


@pytest.mark.parametrize(
    ("choices", "json_type"),
    [
        pytest.param([True, False], "boolean", id="boolean"),
        pytest.param([1, 2], "integer", id="integer"),
        pytest.param([0.5, 1.5], "number", id="number"),
        pytest.param(["a"], "string", id="string"),
        pytest.param([Decimal("1.5")], "any", id="no-json-scalar"),
        pytest.param([], "string", id="none-at-all"),
    ],
)
def test_choices_take_the_json_type_their_values_share(choices: list[Any], json_type: str) -> None:
    assert _one(serializers.ChoiceField(choices=choices)).type == json_type


def test_grouped_choices_are_flattened() -> None:
    found = _one(serializers.ChoiceField(choices=[("Fruit", [("a", "Apple"), ("b", "Banana")])]))
    assert (found.type, found.choices) == ("string", ("a", "b"))


def test_allow_blank_adds_the_empty_string_to_string_choices() -> None:
    found = _one(serializers.ChoiceField(choices=["a", "b"], allow_blank=True))
    assert found.choices == ("a", "b", "")


def test_allow_blank_does_not_list_the_empty_string_twice() -> None:
    found = _one(serializers.ChoiceField(choices=["", "a"], allow_blank=True))
    assert found.choices == ("", "a")


def test_a_nullable_choice_field_is_nullable() -> None:
    found = _one(serializers.ChoiceField(choices=["a"], allow_null=True))
    assert (found.nullable, found.choices) == (True, ("a",))


@pytest.mark.parametrize(
    "field",
    [
        pytest.param(serializers.ChoiceField(choices=[(1, "One"), ("b", "Bee")]), id="mixed"),
        pytest.param(
            serializers.ChoiceField(choices=[(1, "One")], allow_blank=True), id="blank-on-numbers"
        ),
    ],
)
def test_choices_of_no_single_json_type_declare_any_and_no_choices(
    field: serializers.ChoiceField,
) -> None:
    found = _one(field)
    assert (found.type, found.choices, found.choice_labels) == ("any", None, None)


def test_a_multiple_choice_field_is_an_array_whose_elements_are_the_choices() -> None:
    found = _one(serializers.MultipleChoiceField(choices=[("a", "A"), ("b", "B")]))
    assert (found.type, found.items, found.choices) == ("array", "string", ("a", "b"))
    assert found.choice_labels == (("a", "A"), ("b", "B"))
    assert found.min_length is None


def test_a_multiple_choice_field_that_refuses_empty_takes_at_least_one() -> None:
    found = _one(serializers.MultipleChoiceField(choices=["a"], allow_empty=False))
    assert found.min_length == 1


def test_a_multiple_choice_field_of_mixed_choices_is_an_array_of_anything() -> None:
    found = _one(serializers.MultipleChoiceField(choices=[1, "b"]))
    assert (found.type, found.items, found.choices) == ("array", None, None)


# --- lists ------------------------------------------------------------------------


def test_a_list_field_declares_its_element_and_its_length() -> None:
    found = _one(
        serializers.ListField(child=serializers.IntegerField(), min_length=2, max_length=5)
    )
    assert (found.type, found.items, found.min_length, found.max_length) == (
        "array",
        "integer",
        2,
        5,
    )


def test_a_list_field_that_refuses_empty_takes_at_least_one() -> None:
    found = _one(serializers.ListField(child=serializers.IntegerField(), allow_empty=False))
    assert found.min_length == 1


def test_a_list_field_s_own_minimum_outranks_allow_empty() -> None:
    found = _one(
        serializers.ListField(child=serializers.IntegerField(), allow_empty=False, min_length=3)
    )
    assert found.min_length == 3


def test_a_list_of_decimals_declares_each_element_a_decimal() -> None:
    child = serializers.DecimalField(max_digits=5, decimal_places=2)
    found = _one(serializers.ListField(child=child))
    assert (found.items, found.format) == ("string", "decimal")


def test_a_list_of_dates_is_a_list_of_strings_with_no_format() -> None:
    found = _one(serializers.ListField(child=serializers.DateField()))
    assert (found.items, found.format) == ("string", None)


def test_a_list_of_nullable_elements_declares_items_nullable() -> None:
    found = _one(serializers.ListField(child=serializers.CharField(allow_null=True)))
    assert (found.items_nullable, found.nullable) == (True, False)


def test_a_list_of_choices_declares_them_on_the_array() -> None:
    found = _one(serializers.ListField(child=serializers.ChoiceField(choices=[(1, "One")])))
    assert (found.items, found.choices, found.choice_labels) == ("integer", (1,), ((1, "One"),))


@pytest.mark.parametrize(
    ("child", "items"),
    [
        pytest.param(serializers.JSONField(), None, id="any"),
        pytest.param(serializers.FileField(), "file", id="file"),
        pytest.param(serializers.DictField(), "object", id="dict"),
        pytest.param(serializers.ListField(child=serializers.IntegerField()), "array", id="list"),
        pytest.param(_Leaf(many=True), "array", id="list-of-rows"),
        pytest.param(serializers.ChoiceField(choices=[1, "b"]), None, id="mixed-choices"),
    ],
)
def test_a_list_field_s_element_type(child: serializers.Field, items: str | None) -> None:
    assert _one(serializers.ListField(child=child)).items == items


def test_a_list_of_serializers_declares_each_row() -> None:
    class Row(serializers.Serializer):
        title = serializers.CharField()

    found = _one(serializers.ListField(child=Row()))
    assert found.items == Parameters.of(Parameter("title", "string", required=True))


def test_a_list_of_mixed_choices_declares_none() -> None:
    found = _one(serializers.ListField(child=serializers.ChoiceField(choices=[1, "b"])))
    assert (found.items, found.choices, found.choice_labels) == (None, None, None)


def test_a_list_with_no_child_declares_no_element() -> None:
    field = serializers.ListField()
    assert _one(field).items is None


# --- nested serializers ----------------------------------------------------------


class _Address(serializers.Serializer):
    street = serializers.CharField()
    zip = serializers.CharField(required=False)


def test_a_nested_serializer_is_an_object_with_its_own_parameters() -> None:
    found = _one(_Address(allow_null=True, required=False))
    assert (found.type, found.nullable, found.required) == ("object", True, False)
    assert found.fields == Parameters.of(
        Parameter("street", "string", required=True), Parameter("zip", "string")
    )


def test_many_nested_serializers_are_an_array_of_rows() -> None:
    found = _one(_Address(many=True, allow_empty=False, max_length=3))
    assert found.type == "array"
    assert found.items == Parameters.of(
        Parameter("street", "string", required=True), Parameter("zip", "string")
    )
    assert (found.min_length, found.max_length) == (1, 3)


def test_many_nested_serializers_that_take_empty_declare_no_minimum() -> None:
    found = _one(_Address(many=True, min_length=0))
    assert (found.min_length, found.max_length) == (None, None)


@dataclasses.dataclass
class _Row:
    title: str
    qty: int = 1


@dataclasses.dataclass
class _Order:
    name: str
    price: Decimal
    note: str | None = None
    rows: list[_Row] = dataclasses.field(default_factory=list)


def test_a_dataclass_s_rows_are_declared_as_nested_parameters() -> None:
    params = _walk(_Order)
    assert [p.name for p in params] == ["name", "price", "note", "rows"]
    note, rows = params.get("note"), params.get("rows")
    assert note is not None and rows is not None
    assert (note.required, note.nullable) == (False, True)
    assert rows.items == Parameters.of(
        Parameter("title", "string", required=True), Parameter("qty", "integer")
    )


class _Tree(serializers.Serializer):
    name = serializers.CharField()

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        fields["parent"] = _Tree(required=False)
        fields["children"] = _Tree(many=True, required=False)
        return fields


def _depth(param: Parameter | None, step: str) -> int:
    """How many levels of ``step`` a declaration describes before it stops."""
    depth = 0
    while param is not None and param.nested is not None:
        depth += 1
        param = param.nested.get(step)
    return depth


def test_a_recursive_serializer_is_truncated_to_an_open_object() -> None:
    params = _walk(_Tree)
    # The root is the first appearance, and three more nest below it before the
    # next one is truncated: drfs' own schema allowance.
    assert _depth(params.get("parent"), "parent") == 3
    assert _depth(params.get("children"), "children") == 3
    deepest = params.get("parent")
    for _ in range(3):
        assert deepest is not None and deepest.fields is not None
        deepest = deepest.fields.get("parent")
    assert deepest == Parameter("parent", "object")


def test_a_truncated_list_of_rows_is_a_list_of_open_objects() -> None:
    params = _walk(_Tree)
    deepest = params.get("children")
    for _ in range(3):
        assert deepest is not None and isinstance(deepest.items, Parameters)
        deepest = deepest.items.get("children")
    assert deepest == Parameter("children", "array", items="object")
