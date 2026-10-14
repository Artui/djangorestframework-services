"""Tests for ``SerializerPresenter``, the kernel's Presenter over a DRF serializer."""

from __future__ import annotations

import dataclasses
import decimal
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django_service_specs.authorization.unrestricted import Unrestricted
from django_service_specs.dispatch.audience_projection_for_spec import (
    audience_projection_for_spec,
)
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.dispatch.dispatch_result import DispatchResult
from django_service_specs.dispatch.present import present
from django_service_specs.output.field_marking import FieldMarking
from django_service_specs.output.output import Output, OutputField
from django_service_specs.output.presentation_context import PresentationContext
from django_service_specs.pool.pool_seeds import DEFAULT_POOL_SEEDS
from django_service_specs.specs.selector_spec import SelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec
from django_service_specs.types.field_audience import FieldAudience
from django_service_specs.types.selector_kind import SelectorKind
from rest_framework import serializers

from rest_framework_services.audience.build_audience_projection import build_audience_projection
from rest_framework_services.dispatch.build_offline_context import build_offline_context
from rest_framework_services.service_specs.serializer_presenter import SerializerPresenter
from rest_framework_services.types.field_marking import MARKING

STATUSES = [("PENDING_REVIEW", "Awaiting review"), ("PAID", "Paid")]


def _select() -> None: ...


def _spec(
    presenter: SerializerPresenter, kind: SelectorKind = SelectorKind.RETRIEVE
) -> SelectorSpec:
    return SelectorSpec(kind=kind, selector=_select, presenter=presenter)


def _shape(field: OutputField) -> tuple[Any, ...]:
    """The scalar half of one field, so a row of expectations reads as a table."""
    return (field.type, field.format, field.nullable, field.label, field.always_present)


class _Line(serializers.Serializer):
    sku = serializers.CharField(style={MARKING: FieldMarking.handle()})
    tier = serializers.ChoiceField(choices=[("g", "Gold"), ("s", "Silver")])
    description = serializers.CharField()


class _Customer(serializers.Serializer):
    ref = serializers.CharField(style={MARKING: FieldMarking.hidden()})
    name = serializers.CharField(style={MARKING: FieldMarking.label()})


class _Invoice(serializers.Serializer):
    id = serializers.UUIDField(style={MARKING: FieldMarking.handle("Invoice handle.")})
    etag = serializers.CharField(style={MARKING: FieldMarking.hidden()})
    number = serializers.CharField(style={MARKING: FieldMarking.label()})
    due_at = serializers.DateTimeField(style={MARKING: FieldMarking.timestamp()})
    status = serializers.ChoiceField(choices=STATUSES)
    plain = serializers.ChoiceField(choices=["a", "b"])
    note = serializers.CharField()
    lines = _Line(many=True)
    customer = _Customer()
    previous = serializers.ListField(child=_Customer())


# ---------------------------------------------------------------------------
# output(): what the serializer declares
# ---------------------------------------------------------------------------


class _Flat(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(label="Full name")
    nick = serializers.CharField(allow_null=True)
    maybe = serializers.CharField(required=False, allow_null=True)
    ratio = serializers.FloatField(required=False)
    price = serializers.DecimalField(max_digits=5, decimal_places=2)
    at = serializers.DateTimeField()
    on = serializers.DateField()
    when = serializers.TimeField()
    uid = serializers.UUIDField()
    email = serializers.EmailField()
    active = serializers.BooleanField(default=True)
    meta = serializers.DictField()
    secret = serializers.CharField(write_only=True)


def test_output_reads_order_types_formats_labels_and_presence() -> None:
    output = SerializerPresenter(_Flat).output()

    # ``secret`` is write-only: never rendered, so never declared.
    assert output.names() == (
        "id",
        "name",
        "nick",
        "maybe",
        "ratio",
        "price",
        "at",
        "on",
        "when",
        "uid",
        "email",
        "active",
        "meta",
    )
    shapes = {field.name: _shape(field) for field in output}
    assert shapes == {
        # Read-only with no default: DRF drops the key when it cannot read it.
        "id": ("integer", None, False, None, False),
        # An author's label is kept; DRF's derived one (``Nick``) is not.
        "name": ("string", None, False, "Full name", True),
        # ``allow_null`` renders ``None`` rather than dropping the key.
        "nick": ("string", None, True, None, True),
        # Neither required nor defaulted: ``allow_null`` alone keeps the key.
        "maybe": ("string", None, True, None, True),
        "ratio": ("number", None, False, None, False),
        "price": ("string", "decimal", False, None, True),
        "at": ("string", "date-time", False, None, True),
        "on": ("string", "date", False, None, True),
        # Formats the kernel does not name are left off rather than refused.
        "when": ("string", None, False, None, True),
        "uid": ("string", None, False, None, True),
        "email": ("string", None, False, None, True),
        # A default is substituted, so the key is always there.
        "active": ("boolean", None, False, None, True),
        "meta": ("object", None, False, None, True),
    }


def test_a_field_with_no_json_type_is_declared_any() -> None:
    class _Untyped(serializers.Serializer):
        computed = serializers.SerializerMethodField()
        blob = serializers.JSONField()

        def get_computed(self, obj: Any) -> int:
            return 1

    output = SerializerPresenter(_Untyped).output()

    # A computed value or a JSON column may hold any type, and a schema must not
    # claim it is a string.
    assert [(f.name, f.type) for f in output] == [("computed", "any"), ("blob", "any")]


def test_choices_are_value_display_pairs_typed_by_their_values() -> None:
    class _Choices(serializers.Serializer):
        status = serializers.ChoiceField(choices=STATUSES, allow_null=True)
        level = serializers.ChoiceField(choices=[(1, "Low"), (2, "High")])
        flag = serializers.ChoiceField(choices=[(True, "Yes"), (False, "No")])
        half = serializers.ChoiceField(choices=[(0.5, "Half")])
        maybe = serializers.ChoiceField(choices=[(None, "Unset"), ("a", "A")])
        mixed = serializers.ChoiceField(choices=[(1, "One"), ("two", "Two")])
        amount = serializers.ChoiceField(choices=[(decimal.Decimal("1.5"), "One and a half")])
        perms = serializers.MultipleChoiceField(choices=[("r", "Read"), ("w", "Write")])

    output = SerializerPresenter(_Choices).output()

    status = output.get("status")
    assert status is not None
    assert (status.type, status.nullable) == ("string", True)
    assert status.choices == (("PENDING_REVIEW", "Awaiting review"), ("PAID", "Paid"))
    assert {f.name: f.type for f in output} == {
        "status": "string",
        "level": "integer",
        "flag": "boolean",
        "half": "number",
        # ``None`` names no type of its own, so the other values decide.
        "maybe": "string",
        # Values of two types share none, and the field is untyped like any other.
        "mixed": "any",
        # A value JSON has no type for names none either.
        "amount": "any",
        "perms": "array",
    }
    # The kernel lists no choices on an untyped value, so the labels of a mixed
    # set are not declared rather than declared against a type they do not have.
    assert {f.name: f.choices for f in output if f.type == "any"} == {"mixed": None, "amount": None}
    perms = output.get("perms")
    assert perms is not None
    # A list's choices are its element's, as the kernel's other adapters declare them.
    assert (perms.items, perms.choices) == ("string", (("r", "Read"), ("w", "Write")))


class _Row(serializers.Serializer):
    sku = serializers.CharField(style={MARKING: FieldMarking.handle()})


def test_nested_serializers_and_lists_nest_their_output() -> None:
    class _Lists(serializers.Serializer):
        row = _Row(allow_null=True)
        rows = _Row(many=True)
        wrapped = serializers.ListField(child=_Row())
        scores = serializers.ListField(child=serializers.IntegerField(allow_null=True))
        loose = serializers.ListField(child=serializers.JSONField(allow_null=True))
        grid = serializers.ListField(child=serializers.ListField())
        anything = serializers.ListField()
        picks = serializers.ListField(child=serializers.ChoiceField(choices=[("a", "Alpha")]))
        related = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    output = SerializerPresenter(_Lists).output()
    by_name = {field.name: field for field in output}
    row_output = Output((OutputField("sku", "string", marking=FieldMarking.handle()),))

    assert by_name["row"] == OutputField("row", "object", nullable=True, fields=row_output)
    assert by_name["rows"] == OutputField("rows", "array", items=row_output)
    assert by_name["wrapped"] == OutputField("wrapped", "array", items=row_output)
    assert by_name["scores"] == OutputField("scores", "array", items="integer", items_nullable=True)
    # A nullable element of no declared type: the kernel refuses ``items_nullable``
    # without ``items``, so it is left off rather than declared.
    assert by_name["loose"] == OutputField("loose", "array")
    assert by_name["grid"] == OutputField("grid", "array", items="array")
    # An element with no JSON type is left undeclared, which an array allows.
    assert by_name["anything"] == OutputField("anything", "array")
    # drfs' own walk reads no choices off a list's element, so neither does this:
    # declaring them here would have the kernel substitute displays drfs never did.
    assert by_name["picks"] == OutputField("picks", "array", items="string")
    assert by_name["related"] == OutputField("related", "array", always_present=False)


def test_a_serializer_that_nests_itself_is_truncated_rather_than_recursing() -> None:
    class _Node(serializers.Serializer):
        name = serializers.CharField()

        def get_fields(self) -> dict[str, serializers.Field]:
            fields = super().get_fields()
            fields["child"] = _Node(required=False)
            return fields

    field = SerializerPresenter(_Node).output().get("child")
    depth = 1
    while field is not None and field.fields is not None:
        field = field.fields.get("child")
        depth += 1

    # Four appearances on the path are described, as drfs' schema walk allows;
    # the next is a bare object, which is the one thing still known to be true.
    assert depth == 4
    assert field == OutputField("child", "object", always_present=False)


def test_markings_are_read_off_the_style_bag() -> None:
    class _Marked(serializers.Serializer):
        a = serializers.CharField(style={MARKING: FieldMarking.handle()})
        b = serializers.CharField(style={"elsewhere": FieldMarking.hidden()})
        c = serializers.CharField(style={"base_template": "textarea.html"})

    output = SerializerPresenter(_Marked).output()

    assert [f.marking for f in output] == [FieldMarking.handle(), FieldMarking.hidden(), None]


def test_a_value_under_the_marking_key_that_is_not_a_marking_is_refused() -> None:
    class _Half(serializers.Serializer):
        a = serializers.CharField(style={MARKING: "handle"})

    with pytest.raises(ImproperlyConfigured, match=r"_Half\.a: style\['drf_marking'\]"):
        SerializerPresenter(_Half).output()


def test_a_serializer_reading_its_context_can_be_described_and_presented() -> None:
    class _Contextual(serializers.Serializer):
        name = serializers.CharField()

        def get_fields(self) -> dict[str, serializers.Field]:
            # Over HTTP the key is always there; here it is too, holding no request.
            assert self.context["request"] is None
            return super().get_fields()

    presenter = SerializerPresenter(_Contextual)

    assert presenter.output().names() == ("name",)
    assert presenter.present(SimpleNamespace(name="x")) == {"name": "x"}


def test_a_dataclass_is_read_through_the_serializer_it_renders_with() -> None:
    @dataclasses.dataclass
    class _Account:
        id: int = dataclasses.field(
            metadata={"serializer_kwargs": {"style": {MARKING: FieldMarking.handle()}}}
        )
        name: str

    presenter = SerializerPresenter(_Account)

    assert [(f.name, f.type, f.marking) for f in presenter.output()] == [
        ("id", "integer", FieldMarking.handle()),
        ("name", "string", None),
    ]
    assert presenter.present(_Account(id=1, name="Ada")) == {"id": 1, "name": "Ada"}


class _Raw(serializers.BaseSerializer):
    def to_representation(self, instance: Any) -> dict[str, Any]:
        return {"v": instance}


def test_a_base_serializer_declaring_no_fields_outputs_nothing_and_still_presents() -> None:
    presenter = SerializerPresenter(_Raw)

    assert presenter.output() == Output()
    assert presenter.present(3) == {"v": 3}


def test_field_audiences_on_an_output_declaring_no_fields_are_refused() -> None:
    # The output has no field to carry the marking, so honouring it is
    # impossible, and dropping it would present ``v`` to an agent told to hide it.
    presenter = SerializerPresenter(_Raw, field_audiences={"v": FieldMarking.hidden()})

    with pytest.raises(ImproperlyConfigured, match=r"field_audiences names \['v'\]"):
        presenter.output()


@pytest.mark.parametrize("declared", [42, object, _Row()])
def test_anything_but_a_serializer_or_dataclass_class_is_refused(declared: Any) -> None:
    with pytest.raises(ImproperlyConfigured, match="SerializerPresenter takes a serializer"):
        SerializerPresenter(declared)


# ---------------------------------------------------------------------------
# field_audiences: the spec's markings, layered at the top level
# ---------------------------------------------------------------------------


def test_field_audiences_are_layered_over_the_top_level_markings() -> None:
    presenter = SerializerPresenter(
        _Invoice,
        field_audiences={"etag": FieldMarking(), "note": FieldMarking.hidden()},
    )

    output = presenter.output()
    by_name = {field.name: field for field in output}

    # Replaced, added, and left alone.
    assert by_name["etag"].marking == FieldMarking()
    assert by_name["note"].marking == FieldMarking.hidden()
    assert by_name["number"].marking == FieldMarking.label()
    # Only the top level moves: a nested output keeps its own declaration.
    customer = by_name["customer"].fields
    assert customer is not None
    assert customer.get("ref") == OutputField("ref", "string", marking=FieldMarking.hidden())


@pytest.mark.parametrize("name", ["nope", "secret"])
def test_a_field_audience_naming_no_output_field_is_refused(name: str) -> None:
    presenter = SerializerPresenter(_Flat, field_audiences={name: FieldMarking.hidden()})

    # ``secret`` is a write-only field: declared, but never output.
    with pytest.raises(ImproperlyConfigured, match=rf"field_audiences names \['{name}'\]"):
        presenter.output()


def test_a_field_audience_that_is_not_a_marking_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"field_audiences\['etag'\] is 'hidden'"):
        SerializerPresenter(_Invoice, field_audiences={"etag": "hidden"})  # ty: ignore[invalid-argument-type]


def test_a_second_label_from_field_audiences_is_refused_where_it_is_projected() -> None:
    presenter = SerializerPresenter(_Invoice, field_audiences={"note": FieldMarking.label()})

    with pytest.raises(ImproperlyConfigured, match="'number' and 'note' are marked"):
        audience_projection_for_spec(_spec(presenter))


# ---------------------------------------------------------------------------
# The projection: one declaration, read by the kernel as drfs reads it today
# ---------------------------------------------------------------------------


def test_the_projection_equals_drfs_for_a_marked_serializer() -> None:
    projection = audience_projection_for_spec(_spec(SerializerPresenter(_Invoice)))

    assert projection == build_audience_projection(_Invoice)
    # Equal, and not because both are empty.
    assert projection.label == "number"
    assert projection.audience("id") is FieldAudience.HANDLE
    assert projection.choice_labels["status"] == dict(STATUSES)
    assert projection.nested["lines"].audience("sku") is FieldAudience.HANDLE
    assert projection.nested["lines"].choice_labels == {"tier": {"g": "Gold", "s": "Silver"}}
    assert projection.nested["customer"].label == "name"
    assert projection.nested["previous"].audience("ref") is FieldAudience.HIDDEN


_OVERRIDES = {
    # Replaces a marking, moves the label, and marks an unmarked field.
    "etag": FieldMarking(),
    "number": FieldMarking(),
    "plain": FieldMarking.label("The plan."),
    "note": FieldMarking.hidden(),
}


def test_the_projection_equals_drfs_for_spec_level_overrides() -> None:
    projection = audience_projection_for_spec(
        _spec(SerializerPresenter(_Invoice, field_audiences=_OVERRIDES))
    )

    assert projection == build_audience_projection(_Invoice, overrides=_OVERRIDES)
    assert projection.label == "plain"
    assert projection.audience("note") is FieldAudience.HIDDEN
    assert projection.audience("etag") is FieldAudience.CONTENT


def test_the_projection_equals_drfs_for_a_nested_serializer() -> None:
    class _Leaf(serializers.Serializer):
        key = serializers.CharField(style={MARKING: FieldMarking.handle()})
        kind = serializers.ChoiceField(choices=[("x", "Ex")])

    class _Branch(serializers.Serializer):
        leaves = _Leaf(many=True)
        best = _Leaf(allow_null=True)
        quiet = serializers.CharField()

    class _Tree(serializers.Serializer):
        title = serializers.CharField()
        branch = _Branch()
        branches = serializers.ListField(child=_Branch())

    projection = audience_projection_for_spec(_spec(SerializerPresenter(_Tree)))

    assert projection == build_audience_projection(_Tree)
    # Nothing marked at the top, everything two levels down.
    assert projection.fields == {}
    assert projection.nested["branch"].nested["leaves"].audience("key") is FieldAudience.HANDLE
    assert projection.nested["branches"].nested["best"].choice_labels == {"kind": {"x": "Ex"}}


def test_feeding_field_audiences_back_as_overrides_changes_nothing() -> None:
    """A transport that reads ``field_audiences`` off the registry entry and hands
    them back as ``overrides=``, as the published agent transports do, layers them
    over markings that already carry them, and the projection does not move."""
    spec = _spec(SerializerPresenter(_Invoice, field_audiences=_OVERRIDES))

    declared = audience_projection_for_spec(spec)

    assert audience_projection_for_spec(spec, overrides=_OVERRIDES) == declared
    assert build_audience_projection(_Invoice, overrides=_OVERRIDES) == declared


# ---------------------------------------------------------------------------
# present(): the serializer's .data
# ---------------------------------------------------------------------------


def _line(sku: str) -> SimpleNamespace:
    return SimpleNamespace(sku=sku, tier="g", description=f"About {sku}")


def test_present_is_the_serializers_data() -> None:
    presented = SerializerPresenter(_Line).present(_line("A-1"))

    assert presented == {"sku": "A-1", "tier": "g", "description": "About A-1"}


def test_none_is_presented_as_none_rather_than_a_row_of_blanks() -> None:
    assert SerializerPresenter(_Line).present(None) is None


def test_a_list_is_presented_row_by_row_as_drf_renders_many() -> None:
    rows = [_line("A-1"), _line("B-2")]

    presented = present(
        _spec(SerializerPresenter(_Line), SelectorKind.LIST),
        DispatchResult(kind="list", value=rows),
    )

    assert presented == _Line(rows, many=True).data
    assert [row["sku"] for row in presented] == ["A-1", "B-2"]


def test_a_set_of_mixed_choices_is_an_array_of_any_that_keeps_its_choices() -> None:
    class _Mixed(serializers.Serializer):
        picks = serializers.MultipleChoiceField(choices=[(1, "One"), ("two", "Two")])

    picks = SerializerPresenter(_Mixed).output().get("picks")

    # ``items="any"`` is a spelling the kernel refuses: an array with no items
    # already takes any element, and the array may list its choices.
    assert picks == OutputField("picks", "array", choices=((1, "One"), ("two", "Two")))


# ---------------------------------------------------------------------------
# presented(): the serializer under the call's context
# ---------------------------------------------------------------------------


class _Who(serializers.Serializer):
    name = serializers.CharField()
    viewer = serializers.SerializerMethodField()
    request_seen = serializers.SerializerMethodField()
    view_seen = serializers.SerializerMethodField()

    def get_viewer(self, obj: Any) -> Any:
        request = self.context["request"]
        return None if request is None else request.user

    def get_request_seen(self, obj: Any) -> Any:
        return self.context["request"]

    def get_view_seen(self, obj: Any) -> Any:
        return self.context["view"]


def _row(name: str = "x") -> SimpleNamespace:
    return SimpleNamespace(name=name)


def test_presented_reads_the_principal_off_a_request_it_builds_when_none_is_registered() -> None:
    principal = object()

    payload = SerializerPresenter(_Who).presented(_row(), PresentationContext(principal))

    assert payload["viewer"] is principal
    assert payload["request_seen"].user is principal
    assert payload["view_seen"].request is payload["request_seen"]


def test_presented_hands_the_serializer_the_registered_request_and_view() -> None:
    principal = object()
    offline = build_offline_context(principal, {})
    context = PresentationContext(
        principal,
        ambient=MappingProxyType({"request": offline.request, "view": offline.view}),
    )

    payload = SerializerPresenter(_Who).presented(_row(), context)

    assert payload["request_seen"] is offline.request
    assert payload["view_seen"] is offline.view


def test_presented_with_no_context_is_the_context_free_present() -> None:
    presenter = SerializerPresenter(_Who)

    payload = presenter.presented(_row(), None)

    # No principal and no request, as ``present`` has always rendered.
    assert payload["request_seen"] is None and payload["viewer"] is None
    assert payload == presenter.present(_row())


def test_present_delegates_and_presented_is_what_the_kernel_calls() -> None:
    calls: list[Any] = []

    class Spy(SerializerPresenter):
        def presented(self, value: Any, context: Any) -> Any:
            calls.append(context)
            return super().presented(value, context)

    Spy(_Who).present(_row())

    # ``present`` is the context-free form: it asks ``presented`` with no context.
    assert calls == [None]


def test_presented_none_is_none_whatever_the_context() -> None:
    assert SerializerPresenter(_Who).presented(None, PresentationContext(object())) is None


@pytest.mark.django_db
def test_dispatch_carries_the_call_s_context_to_a_selector_spec_s_serializer() -> None:
    user = get_user_model().objects.create(username="ada")
    offline = build_offline_context(user, {})
    seeds = DEFAULT_POOL_SEEDS.extend(request=lambda: offline.request, view=lambda: offline.view)
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda: SimpleNamespace(name="x"),
        permissions=[Unrestricted()],
        presenter=SerializerPresenter(_Who),
    )

    result = dispatch(spec, principal=user, arguments={}, pool_seeds=seeds)
    payload = present(spec, result)

    assert result.context is not None and result.context.principal is user
    assert payload["request_seen"] is offline.request
    assert payload["view_seen"] is offline.view
    assert payload["viewer"] is user


@pytest.mark.django_db
def test_dispatch_carries_the_call_s_context_to_a_list_and_a_service_spec() -> None:
    user = get_user_model().objects.create(username="grace")
    offline = build_offline_context(user, {})
    seeds = DEFAULT_POOL_SEEDS.extend(request=lambda: offline.request)
    listing = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=lambda: [_row("a"), _row("b")],
        permissions=[Unrestricted()],
        presenter=SerializerPresenter(_Who),
    )
    service = ServiceSpec(
        service=lambda: _row("made"),
        permissions=[Unrestricted()],
        presenter=SerializerPresenter(_Who),
    )

    rows = present(listing, dispatch(listing, principal=user, arguments={}, pool_seeds=seeds))
    made = present(service, dispatch(service, principal=user, arguments={}, pool_seeds=seeds))

    assert [row["request_seen"] for row in rows] == [offline.request, offline.request]
    assert made["request_seen"] is offline.request
