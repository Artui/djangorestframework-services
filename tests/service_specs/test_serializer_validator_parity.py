"""``SerializerValidator``'s Parameters against drfs' own input schema for the same serializer.

Each serializer below is described twice: by drfs'
``serializer_to_json_schema``, and by the kernel's ``parameters_schema`` over
``SerializerValidator(...).parameters()``. At every object both describe - the
top, each nested serializer, each row of a list of serializers - the two must
name the same properties and require the same ones, in the same order. The
comparison runs under ``UnknownArguments.IGNORE``, where the kernel states no
``additionalProperties``, and on a whole call, since drfs' ``partial=True``
drops ``required`` and the kernel's schema leaves partiality to the shape
check.

**Where the names differ, on purpose.** One case, asserted by name rather than
skipped:

- **A** ``HiddenField`` is a property in drfs' schema, written ``{}``, and no
  Parameter here. DRF reads only its default and ignores what a caller sends,
  so drfs advertised an argument that does nothing; under the kernel's closed
  argument set (``REJECT``) a caller sending one is now refused as unknown.

**Where the two dialects differ in what a property says**, none of which
touches a name or ``required``:

- **Nullability.** The kernel states it, as ``[t, "null"]``; drfs' serializer
  walk states it only on a choice field's ``oneOf``.
- **Titles.** drfs writes an author's ``label`` as ``title``; a Parameter has no
  label, so the kernel writes none. Choice labels survive in both, as titles.
- **Formats.** The walk states three (``decimal``, ``date``, ``date-time``)
  of the kernel's four, leaving ``uuid`` unstated; drfs also writes ``time``,
  ``uuid``, ``email`` and ``uri``, which the kernel states as a plain string. Where drfs writes ``date`` or ``date-time`` for a
  field reading formats other than ISO 8601, and ``decimal`` for a localized
  decimal, the kernel states no format and no type respectively: its check
  runs in front of the field, so it may not refuse what the field reads
  (``test_the_shape_check_passes_what_the_field_takes``), where drfs' schema
  only describes.
- **Choices.** drfs writes ``oneOf`` of ``const``/``title`` and widens with
  ``""`` and ``null`` as constants; the kernel writes ``anyOf`` of the same
  pairs, ``""`` among the choices, and ``null`` from ``nullable``.
- **Lists.** The kernel states a list's ``minItems`` and ``maxItems`` - its
  declared length, or the ``SERVICE_SPECS_MAX_ARRAY_LENGTH`` default where
  none is declared - and drfs states neither for a ``ListField``. A list of
  related keys is ``{"type": "array"}`` here and ``{}`` in drfs.
- **A dataclass's rows** are described here, as the ``DataclassSerializer``
  that validates them reads them; drfs' annotation walk writes them as
  ``items: {}``. The comparison stops where drfs stops describing.
- **Consumer rules.** A ``JsonSchemaRegistry``'s ``fields`` rules reach both,
  and a registered field's node agrees in both but for a ``format`` outside
  the kernel's four, which drfs advertises and the kernel drops
  (test_a_registered_field_s_node_agrees_but_for_a_format_the_kernel_lacks).
  drf-spectacular's ``extend_schema_field`` and ``extend_schema_serializer``
  overrides, including ``exclude_fields``, which can remove a property, reach
  drfs alone, so a serializer using them is described here as declared.
- **Depth.** drfs' ``max_depth`` has no counterpart; the recursion allowance is
  the same four appearances in both, so a recursive serializer truncates at
  the same node.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any

import pytest
from django_service_specs.schema.parameters_schema import parameters_schema
from django_service_specs.types.unknown_arguments import UnknownArguments
from rest_framework import serializers
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services.jsonschema.serializer_to_json_schema import serializer_to_json_schema
from rest_framework_services.service_specs.serializer_validator import SerializerValidator
from rest_framework_services.service_specs.utils import describe_serializer, input_parameters
from rest_framework_services.types.json_schema_registry import JsonSchemaRegistry
from tests.testapp.models import Post


class _Flat(serializers.Serializer):
    title = serializers.CharField(label="Headline", help_text="The title.")
    count = serializers.IntegerField(default=3)
    price = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    status = serializers.ChoiceField(choices=[(1, "One"), (2, "Two")], allow_null=True)
    kinds = serializers.MultipleChoiceField(choices=["a", "b"])
    tags = serializers.ListField(child=serializers.CharField(), max_length=5)
    blob = serializers.JSONField(required=False)
    extra = serializers.DictField(required=False)
    upload = serializers.FileField(required=False)
    secret = serializers.CharField(write_only=True)
    owner = serializers.HiddenField(default=serializers.CurrentUserDefault())
    computed = serializers.SerializerMethodField()
    fixed = serializers.CharField(read_only=True)

    def get_computed(self, obj: Any) -> int:
        return 1


class _Address(serializers.Serializer):
    street = serializers.CharField()
    zip = serializers.CharField(required=False)
    by = serializers.HiddenField(default=None)


class _Nested(serializers.Serializer):
    name = serializers.CharField()
    home = _Address()
    work = _Address(required=False, allow_null=True)
    previous = _Address(many=True)
    listed = serializers.ListField(child=_Address(), required=False)


class _PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ("id", "title", "body", "published", "views", "author", "tags")


@dataclasses.dataclass
class _Row:
    title: str
    qty: int = 1


@dataclasses.dataclass
class _Order:
    name: str
    price: Decimal
    note: str | None = None
    tags: list[str] = dataclasses.field(default_factory=list)
    rows: list[_Row] = dataclasses.field(default_factory=list)


class _OrderSerializer(DataclassSerializer):
    """A declared DataclassSerializer, as the gallery writes its inputs."""

    class Meta:
        dataclass = _Order


class _Tree(serializers.Serializer):
    name = serializers.CharField()

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        fields["parent"] = _Tree(required=False)
        fields["children"] = _Tree(many=True, required=False)
        return fields


def _objects(schema: dict[str, Any], path: str = "") -> dict[str, dict[str, Any]]:
    """Every described object node in ``schema``, by its path: the top, nested objects and rows."""
    found: dict[str, dict[str, Any]] = {}
    if "properties" in schema:
        found[path] = schema
        for name, child in schema["properties"].items():
            found.update(_objects(child, f"{path}.{name}" if path else name))
    items = schema.get("items")
    if isinstance(items, dict):
        found.update(_objects(items, f"{path}[]"))
    return found


_EXPECTED_HIDDEN: dict[Any, dict[str, set[str]]] = {
    _Flat: {"": {"owner"}},
    _Nested: {"home": {"by"}, "work": {"by"}, "previous[]": {"by"}, "listed[]": {"by"}},
}


@pytest.mark.parametrize(
    "declaration",
    [
        pytest.param(_Flat, id="flat"),
        pytest.param(_Nested, id="nested"),
        pytest.param(_PostSerializer, id="model-serializer"),
        pytest.param(_Order, id="dataclass"),
        pytest.param(_OrderSerializer, id="dataclass-serializer"),
        pytest.param(_Tree, id="recursive"),
    ],
)
def test_names_and_required_agree_at_every_object_both_describe(declaration: Any) -> None:
    kernel = _objects(
        parameters_schema(
            SerializerValidator(declaration).parameters(),
            unknown_arguments=UnknownArguments.IGNORE,
        )
    )
    drfs = _objects(serializer_to_json_schema(declaration))
    hidden = _EXPECTED_HIDDEN.get(declaration, {})
    # The comparison stops where drfs stops describing (a dataclass's rows),
    # and nowhere else: every object drfs describes, the kernel describes too.
    assert set(drfs) <= set(kernel)
    assert set(drfs), "the comparison compared nothing"
    for path, theirs in drfs.items():
        ours = kernel[path]
        their_names = list(theirs["properties"])
        expected_hidden = hidden.get(path, set())
        # The one named difference: drfs lists a HiddenField, the kernel does not.
        assert {n for n in their_names if n not in ours["properties"]} == expected_hidden, path
        assert list(ours["properties"]) == [n for n in their_names if n not in expected_hidden], (
            path
        )
        assert ours.get("required", []) == theirs.get("required", []), path


def test_the_recursive_serializer_truncates_at_the_same_node_in_both() -> None:
    kernel = _objects(
        parameters_schema(
            SerializerValidator(_Tree).parameters(), unknown_arguments=UnknownArguments.IGNORE
        )
    )
    drfs = _objects(serializer_to_json_schema(_Tree))
    assert set(kernel) == set(drfs)
    assert "parent.parent.parent" in kernel
    assert "parent.parent.parent.parent" not in kernel


def test_a_dataclass_s_rows_are_described_where_drfs_stops() -> None:
    kernel = _objects(
        parameters_schema(
            SerializerValidator(_Order).parameters(), unknown_arguments=UnknownArguments.IGNORE
        )
    )
    drfs = _objects(serializer_to_json_schema(_Order))
    assert set(kernel) - set(drfs) == {"rows[]"}
    assert kernel["rows[]"]["required"] == ["title"]


class _MoneyField(serializers.CharField): ...


class _CountField(serializers.IntegerField): ...


class _Registered(serializers.Serializer):
    amount = _MoneyField(help_text="What it costs.")
    count = _CountField()
    note = serializers.CharField(required=False)


_MONEY = {
    "type": "string",
    "format": "money",
    "pattern": "^[0-9]+$",
    "minLength": 1,
    "maxLength": 12,
    "description": "Cents, as digits.",
}
_COUNT = {"type": "integer", "minimum": 0, "maximum": 99, "title": "Count"}
_REGISTRY = JsonSchemaRegistry(fields=((_MoneyField, _MONEY), (_CountField, _COUNT)))


def test_a_registered_field_s_node_agrees_but_for_a_format_the_kernel_lacks() -> None:
    kernel = parameters_schema(
        input_parameters(describe_serializer(_Registered), registry=_REGISTRY),
        unknown_arguments=UnknownArguments.IGNORE,
    )
    drfs = serializer_to_json_schema(_Registered, registry=_REGISTRY)
    assert kernel["required"] == drfs["required"] == ["amount", "count"]
    ours, theirs = kernel["properties"], drfs["properties"]
    # The one difference: ``money`` is no format the kernel has, so the compiled
    # spec drops it, and drfs' own schema keeps advertising it.
    assert theirs["amount"]["format"] == "money"
    assert "format" not in ours["amount"]
    assert ours["amount"] == {
        key: value for key, value in theirs["amount"].items() if key != "format"
    }
    # The field's own help text is the description in both, over the rule's.
    assert ours["amount"]["description"] == "What it costs."
    assert ours["count"] == theirs["count"] == _COUNT
    assert ours["note"] == theirs["note"]
