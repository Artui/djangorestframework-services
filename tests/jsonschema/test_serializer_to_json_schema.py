"""Tests for ``serializer_to_json_schema``."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest
from rest_framework import serializers

from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.jsonschema.serializer_to_json_schema import serializer_to_json_schema


class _S(serializers.Serializer):
    name = serializers.CharField()
    count = serializers.IntegerField(required=False)


@dataclasses.dataclass
class _DC:
    name: str
    count: int = 0


def test_none_is_empty_object() -> None:
    assert serializer_to_json_schema(None) == {"type": "object"}


def test_serializer_subclass_is_walked() -> None:
    schema = serializer_to_json_schema(_S)
    assert schema["properties"]["name"] == {"type": "string"}
    assert schema["required"] == ["name"]


def test_dataclass_is_walked() -> None:
    schema = serializer_to_json_schema(_DC)
    assert schema["properties"]["count"] == {"type": "integer"}
    assert schema["required"] == ["name"]


def test_other_type_is_refused_rather_than_described_as_empty() -> None:
    """It used to return ``{"type": "object"}`` — byte-identical to a spec with no
    input at all, so a tool advertised no arguments and nothing warned. Meanwhile
    ``build_input_serializer_from_data`` raised ``TypeError`` for the same class,
    so the schema promised a call the dispatcher would refuse."""

    class Plain: ...

    with pytest.raises(TypeError, match="dataclass type or a Serializer subclass"):
        serializer_to_json_schema(Plain)


def test_no_serializer_is_still_an_empty_object() -> None:
    """The legitimate empty case keeps its schema; only the unrecognised one raises."""
    assert serializer_to_json_schema(None) == {"type": "object"}


def test_partial_drops_required() -> None:
    schema = serializer_to_json_schema(_S, partial=True)
    assert "required" not in schema
    assert schema["properties"]["name"] == {"type": "string"}


def test_registry_is_forwarded_to_fields() -> None:
    from rest_framework_services.types.json_schema_registry import DEFAULT_JSON_SCHEMA_REGISTRY

    class _MoneyField(serializers.Field): ...

    class _Order(serializers.Serializer):
        total = _MoneyField()

    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
        fields=[(_MoneyField, {"type": "string", "format": "money"})]
    )
    schema = serializer_to_json_schema(_Order, registry=registry)
    assert schema["properties"]["total"] == {"type": "string", "format": "money"}


class _ContextAware(serializers.Serializer):
    """A serializer whose field set depends on who is asking.

    Routine over HTTP, where DRF's context always carries a request -- and the
    reason ``build_audience_projection`` synthesizes the same baseline before it
    instantiates. Schema generation instantiated bare and raised ``KeyError``
    on the identical serializer, so a spec could be dispatched and rendered but
    not described.
    """

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        request = self.context["request"]
        fields["title"] = serializers.CharField()
        if getattr(request, "is_staff", False):
            fields["internal_note"] = serializers.CharField()
        return fields


def test_serializer_reading_request_context_is_described() -> None:
    schema = serializer_to_json_schema(_ContextAware)

    assert schema["properties"]["title"] == {"type": "string"}


def test_context_carries_drfs_own_keys() -> None:
    class _ReadsView(serializers.Serializer):
        def get_fields(self) -> dict[str, serializers.Field]:
            fields = super().get_fields()
            # Present and ``None``, exactly as off-HTTP dispatch renders it --
            # a ``KeyError`` here would be the same defect one key over.
            assert self.context["view"] is None
            assert self.context["format"] is None
            fields["ok"] = serializers.BooleanField()
            return fields

    assert serializer_to_json_schema(_ReadsView)["properties"]["ok"] == {"type": "boolean"}


def test_label_travels_as_title() -> None:
    class _Labelled(serializers.Serializer):
        vat_id = serializers.CharField(label="VAT registration number")

    assert serializer_to_json_schema(_Labelled)["properties"]["vat_id"]["title"] == (
        "VAT registration number"
    )


def test_the_label_drf_derives_from_the_name_is_not_repeated() -> None:
    class _Plain(serializers.Serializer):
        vat_id = serializers.CharField()

    # DRF binds ``label="Vat id"`` to every unlabelled field. Emitting that
    # restates the property name in worse English and costs a reader tokens to
    # learn nothing.
    assert "title" not in serializer_to_json_schema(_Plain)["properties"]["vat_id"]


class _NestedIn(serializers.Serializer):
    inner = _S()


class TestMaxDepth:
    def test_unset_describes_the_nested_serializer(self) -> None:
        schema = serializer_to_json_schema(_NestedIn)

        assert schema["properties"]["inner"]["properties"]["name"] == {"type": "string"}

    def test_the_bound_truncates_the_nested_serializer(self) -> None:
        assert serializer_to_json_schema(_NestedIn, max_depth=1) == {
            "type": "object",
            "properties": {"inner": {"type": "object"}},
            "required": ["inner"],
        }

    def test_the_bound_composes_with_partial(self) -> None:
        """``partial`` drops ``required``; the bound is about depth, not fields."""
        assert serializer_to_json_schema(_NestedIn, partial=True, max_depth=1) == {
            "type": "object",
            "properties": {"inner": {"type": "object"}},
        }


def test_a_uniform_enum_declares_its_type() -> None:
    """A bare ``enum`` is silently lossy for a generator keyed on ``type``.

    A CLI or form builder reading ``type`` to pick a widget saw nothing and
    degraded the field to free text, while the values were right there.
    """

    class _S(serializers.Serializer):
        status = serializers.ChoiceField(choices=["draft", "live"])
        rank = serializers.ChoiceField(choices=[1, 2])

    schema = serializer_to_json_schema(_S)
    assert schema["properties"]["status"] == {"type": "string", "enum": ["draft", "live"]}
    assert schema["properties"]["rank"] == {"type": "integer", "enum": [1, 2]}


def test_a_mixed_enum_declares_no_type() -> None:
    """Incomplete rather than false: there is no one type to name."""

    class _S(serializers.Serializer):
        mixed = serializers.ChoiceField(choices=["a", 1])

    assert serializer_to_json_schema(_S)["properties"]["mixed"] == {"enum": ["a", 1]}


def test_a_nullable_enum_declares_no_type() -> None:
    """``None`` is widened into the values, so the set is no longer one type."""

    class _S(serializers.Serializer):
        status = serializers.ChoiceField(choices=["draft"], allow_null=True)

    assert "type" not in serializer_to_json_schema(_S)["properties"]["status"]


def test_a_declared_default_reaches_the_schema() -> None:
    """It never did, so a client could not tell an optional field's resting value."""

    class _S(serializers.Serializer):
        status = serializers.CharField(default="draft")
        quantity = serializers.IntegerField(default=0)
        name = serializers.CharField()

    props = serializer_to_json_schema(_S)["properties"]
    assert props["status"]["default"] == "draft"
    assert props["quantity"]["default"] == 0
    assert "default" not in props["name"]


def test_a_callable_default_is_not_published() -> None:
    """A callable default is not a constant, so naming one would be a false claim."""

    class _S(serializers.Serializer):
        when = serializers.CharField(default=lambda: "computed")

    assert "default" not in serializer_to_json_schema(_S)["properties"]["when"]


def test_a_default_that_is_not_json_is_not_published() -> None:
    """The schema is serialised to JSON by both transports; a Decimal would break it."""

    class _S(serializers.Serializer):
        amount = serializers.DecimalField(max_digits=5, decimal_places=2, default=Decimal("1.5"))

    assert "default" not in serializer_to_json_schema(_S)["properties"]["amount"]


def test_an_output_schema_carries_no_default() -> None:
    """A default says what happens when input omits the field; on output it means nothing."""

    class _S(serializers.Serializer):
        status = serializers.CharField(default="draft")

    schema = output_to_json_schema(_S)
    assert "default" not in schema["properties"]["status"]


def test_a_boolean_enum_declares_boolean_not_integer() -> None:
    """``bool`` is a subclass of ``int``, so the order of the checks is the claim."""

    class _S(serializers.Serializer):
        flag = serializers.ChoiceField(choices=[True, False])

    assert serializer_to_json_schema(_S)["properties"]["flag"]["type"] == "boolean"


def test_a_float_enum_declares_number() -> None:
    class _S(serializers.Serializer):
        rate = serializers.ChoiceField(choices=[1.5, 2.5])

    assert serializer_to_json_schema(_S)["properties"]["rate"]["type"] == "number"


def test_a_container_default_is_published_when_every_member_is_json() -> None:
    class _S(serializers.Serializer):
        tags = serializers.ListField(child=serializers.CharField(), default=["a", "b"])
        meta = serializers.DictField(default={"k": "v"})

    props = serializer_to_json_schema(_S)["properties"]
    assert props["tags"]["default"] == ["a", "b"]
    assert props["meta"]["default"] == {"k": "v"}


def test_a_container_default_holding_a_non_json_member_is_not_published() -> None:
    """The check recurses, because a list is only as serialisable as its members."""

    class _S(serializers.Serializer):
        amounts = serializers.ListField(
            child=serializers.DecimalField(max_digits=5, decimal_places=2),
            default=[Decimal("1.5")],
        )

    assert "default" not in serializer_to_json_schema(_S)["properties"]["amounts"]


def test_a_callable_that_is_also_json_native_is_not_published() -> None:
    """The case the ``callable`` check exists for, and the only one that holds it.

    A lambda is already excluded for not being JSON-native, so mutating the
    ``callable`` test away leaves every other default test passing. What it
    uniquely catches is a default that is *both* callable and a JSON container —
    the shape of DRF's own ``CreateOnlyDefault``, a callable wrapping a value.
    Publishing its container contents would name a constant the field does not
    actually default to.
    """

    class _CallableDict(dict):
        def __call__(self) -> dict[str, int]:
            return {"computed": 1}

    class _S(serializers.Serializer):
        meta = serializers.DictField(default=_CallableDict(stale=0))

    assert "default" not in serializer_to_json_schema(_S)["properties"]["meta"]
