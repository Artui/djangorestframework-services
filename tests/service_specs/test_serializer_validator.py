"""Tests for ``SerializerValidator``."""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from typing import Any, ClassVar

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django_service_specs.authorization.unrestricted import Unrestricted
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from django_service_specs.parameters.parameters import Parameter, Parameters
from django_service_specs.pool.pool_seeds import DEFAULT_POOL_SEEDS
from django_service_specs.specs.service_spec import ServiceSpec
from django_service_specs.validation.validation_context import ValidationContext
from django_service_specs.validation.validator import Validator
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail, ValidationError
from rest_framework.request import Request
from rest_framework.validators import UniqueValidator
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services.dispatch.build_offline_context import build_offline_context
from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from rest_framework_services.service_specs.serializer_validator import SerializerValidator
from tests.testapp.models import Post


class _Note(serializers.Serializer):
    title = serializers.CharField(max_length=5)
    count = serializers.IntegerField(required=False)


@dataclasses.dataclass
class _Row:
    title: str
    qty: int = 1


@dataclasses.dataclass
class _Order:
    name: str
    rows: list[_Row] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class _MoveIn:
    event_id: int
    start_hour: int | None = None
    day: str | None = None


class _MoveInSerializer(DataclassSerializer):
    """The gallery's shape: a declared DataclassSerializer whose ``validate`` reads the instance."""

    class Meta:
        dataclass = _MoveIn

    def validate(self, attrs: _MoveIn) -> _MoveIn:
        if (attrs.day is None) != (attrs.start_hour is None):
            raise serializers.ValidationError("Pass both day and start_hour, or neither.")
        return attrs


def _context(
    principal: Any = None, target: Any = None, *, partial: bool = False
) -> ValidationContext:
    return ValidationContext(principal, target, partial=partial)


# --- the declaration --------------------------------------------------------------


def test_it_is_the_kernel_s_validator() -> None:
    assert isinstance(SerializerValidator(_Note), Validator)


@pytest.mark.parametrize(
    "declaration",
    [
        pytest.param(_Note(), id="an-instance"),
        pytest.param(dict, id="another-class"),
    ],
)
def test_anything_but_a_serializer_class_or_a_dataclass_is_refused_when_declared(
    declaration: Any,
) -> None:
    with pytest.raises(ImproperlyConfigured, match="SerializerValidator"):
        SerializerValidator(declaration)


def test_parameters_are_the_walked_fields() -> None:
    assert SerializerValidator(_Note).parameters() == Parameters.of(
        Parameter("title", "string", required=True), Parameter("count", "integer")
    )


def test_the_declaration_is_read_on_first_use_not_when_constructed() -> None:
    walked: list[str] = []

    class Lazy(serializers.Serializer):
        def get_fields(self) -> dict[str, serializers.Field]:
            walked.append("walked")
            return {"name": serializers.CharField()}

    validator = SerializerValidator(Lazy)
    assert walked == []
    assert validator.parameters().names() == {"name"}


# --- validate -----------------------------------------------------------------


def test_validate_returns_the_validated_values() -> None:
    values = SerializerValidator(_Note).validate({"title": "hi", "count": 2}, _context())
    assert values == {"title": "hi", "count": 2}
    assert type(values) is dict


def test_a_value_is_keyed_by_its_source_attribute() -> None:
    class S(serializers.Serializer):
        headline = serializers.CharField(source="title")

    assert SerializerValidator(S).validate({"headline": "hi"}, _context()) == {"title": "hi"}


def test_a_refusal_is_invalid_arguments_carrying_drf_s_detail() -> None:
    with pytest.raises(InvalidArguments) as raised:
        SerializerValidator(_Note).validate({"title": "too long", "count": "x"}, _context())
    detail = raised.value.detail
    assert detail == {
        "title": ["Ensure this field has no more than 5 characters."],
        "count": ["A valid integer is required."],
    }
    # DRF's own leaves, so a program still reads each mistake's code.
    assert isinstance(detail["title"][0], ErrorDetail)
    assert [d.code for d in (detail["title"][0], detail["count"][0])] == ["max_length", "invalid"]


def test_a_cross_field_refusal_sits_under_non_field_errors() -> None:
    class S(serializers.Serializer):
        a = serializers.IntegerField()

        def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
            raise serializers.ValidationError("No.")

    with pytest.raises(InvalidArguments) as raised:
        SerializerValidator(S).validate({"a": 1}, _context())
    assert raised.value.detail == {"non_field_errors": ["No."]}


def test_a_hidden_current_user_default_reads_the_principal() -> None:
    class S(serializers.Serializer):
        title = serializers.CharField()
        owner = serializers.HiddenField(default=serializers.CurrentUserDefault())

    principal = object()
    values = SerializerValidator(S).validate({"title": "hi"}, _context(principal))
    assert values == {"title": "hi", "owner": principal}


def test_the_serializer_context_carries_an_offline_request_for_the_principal() -> None:
    seen: dict[str, Any] = {}

    class S(serializers.Serializer):
        title = serializers.CharField()

        def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
            seen.update(self.context)
            return attrs

    principal = object()
    SerializerValidator(S).validate({"title": "hi"}, _context(principal))
    request = seen["request"]
    assert isinstance(request, Request)
    assert request.user is principal
    # The arguments are the request's data too, as dispatch_spec hands both.
    assert request.data == {"title": "hi"}
    assert seen["view"].request is request
    assert seen["format"] is None


def test_a_partial_call_takes_a_required_field_left_out_and_returns_what_was_sent() -> None:
    values = SerializerValidator(_Note).validate({"count": 2}, _context(partial=True))
    assert values == {"count": 2}


def test_a_whole_call_refuses_a_required_field_left_out() -> None:
    with pytest.raises(InvalidArguments) as raised:
        SerializerValidator(_Note).validate({"count": 2}, _context())
    assert raised.value.detail == {"title": ["This field is required."]}


# --- the target -------------------------------------------------------------------


class _UniqueTitle(serializers.Serializer):
    title = serializers.CharField(validators=[UniqueValidator(queryset=Post.objects.all())])


@pytest.mark.django_db
def test_an_update_s_uniqueness_check_excludes_the_target() -> None:
    post = Post.objects.create(title="taken")
    assert SerializerValidator(_UniqueTitle).validate(
        {"title": "taken"}, _context(target=post)
    ) == {"title": "taken"}


@pytest.mark.django_db
def test_a_create_s_uniqueness_check_refuses_a_taken_value() -> None:
    Post.objects.create(title="taken")
    with pytest.raises(InvalidArguments) as raised:
        SerializerValidator(_UniqueTitle).validate({"title": "taken"}, _context())
    assert [d.code for d in raised.value.detail["title"]] == ["unique"]


@pytest.mark.django_db
def test_a_collection_target_binds_as_a_create() -> None:
    Post.objects.create(title="taken")
    with pytest.raises(InvalidArguments) as raised:
        SerializerValidator(_UniqueTitle).validate(
            {"title": "taken"}, _context(target=Post.objects.all())
        )
    assert [d.code for d in raised.value.detail["title"]] == ["unique"]


@pytest.mark.django_db
def test_a_model_serializer_s_own_uniqueness_check_excludes_the_target() -> None:
    user_model = get_user_model()

    class S(serializers.ModelSerializer):
        class Meta:
            model = user_model
            fields = ("username",)

    user = user_model.objects.create(username="ada")
    validator = SerializerValidator(S)
    assert validator.validate({"username": "ada"}, _context(target=user)) == {"username": "ada"}
    with pytest.raises(InvalidArguments):
        validator.validate({"username": "ada"}, _context())


# --- validated: data and the bound serializer -----------------------------------


def test_validated_hands_the_validated_data_and_binds_the_serializer() -> None:
    target = object()
    validated = SerializerValidator(_Note).validated({"title": "hi"}, _context(target=target))
    assert validated.values == {"title": "hi"}
    assert validated.data == {"title": "hi"}
    serializer = validated.pool["serializer"]
    assert isinstance(serializer, _Note)
    assert serializer.instance is target
    assert serializer.validated_data is validated.data
    assert set(validated.pool) == {"serializer"}


def test_validate_is_validated_s_values() -> None:
    validator = SerializerValidator(_Note)
    assert validator.validate({"title": "hi"}, _context()) == (
        validator.validated({"title": "hi"}, _context()).values
    )


def test_a_dataclass_s_data_is_its_instance_and_its_values_its_fields() -> None:
    validated = SerializerValidator(_Order).validated(
        {"name": "n", "rows": [{"title": "t"}]}, _context()
    )
    assert validated.data == _Order(name="n", rows=[_Row(title="t", qty=1)])
    assert validated.values == {"name": "n", "rows": [_Row(title="t", qty=1)]}
    assert validated.values["rows"] is validated.data.rows
    assert isinstance(validated.pool["serializer"], DataclassSerializer)


def test_an_object_a_validate_returns_is_data_and_spreads_nothing() -> None:
    built = object()

    class S(serializers.Serializer):
        title = serializers.CharField()

        def validate(self, attrs: dict[str, Any]) -> Any:
            return built

    validated = SerializerValidator(S).validated({"title": "hi"}, _context())
    assert (validated.data, validated.values) == (built, {})


def test_a_dataclass_s_row_refusal_names_the_row() -> None:
    with pytest.raises(InvalidArguments) as raised:
        SerializerValidator(_Order).validate(
            {"name": "n", "rows": [{"title": "t"}, {}]}, _context()
        )
    # A dataclass's list is a ListField, whose errors DRF keys by the row's index.
    assert raised.value.detail["rows"] == {1: {"title": ["This field is required."]}}


@pytest.mark.parametrize(
    "declaration",
    [pytest.param(_Order, id="a-dataclass"), pytest.param(_MoveInSerializer, id="its-serializer")],
)
def test_a_partial_call_over_a_dataclass_is_refused(declaration: Any) -> None:
    with pytest.raises(ImproperlyConfigured, match="partial"):
        SerializerValidator(declaration).validate({"event_id": 1}, _context(partial=True))


def test_a_declared_dataclass_serializer_hands_its_instance_and_its_fields() -> None:
    validated = SerializerValidator(_MoveInSerializer).validated(
        {"event_id": 7, "day": "mon", "start_hour": 9}, _context()
    )
    assert validated.data == _MoveIn(event_id=7, start_hour=9, day="mon")
    assert validated.values == {"event_id": 7, "start_hour": 9, "day": "mon"}
    assert type(validated.pool["serializer"]) is _MoveInSerializer


def test_a_declared_dataclass_serializer_s_own_validate_refuses() -> None:
    with pytest.raises(InvalidArguments) as raised:
        SerializerValidator(_MoveInSerializer).validate({"event_id": 7, "day": "mon"}, _context())
    assert raised.value.detail == {
        "non_field_errors": ["Pass both day and start_hour, or neither."]
    }


def test_supports_partial_is_answered_per_declaration() -> None:
    # The kernel reads it from the instance, so one adapter class answers for
    # each declaration it was built over: held by the three declarations
    # (a Serializer, a dataclass, a declared DataclassSerializer) failing
    # one conjunct each.
    assert SerializerValidator(_Note).supports_partial is True
    assert SerializerValidator(_Order).supports_partial is False
    assert SerializerValidator(_MoveInSerializer).supports_partial is False


@pytest.mark.parametrize(
    "declaration",
    [pytest.param(_Order, id="a-dataclass"), pytest.param(_MoveInSerializer, id="its-serializer")],
)
def test_a_partial_spec_over_a_dataclass_is_refused_when_it_is_built(declaration: Any) -> None:
    with pytest.raises(ImproperlyConfigured, match="partial"):
        ServiceSpec(
            service=lambda data: data,
            permissions=[Unrestricted()],
            validator=SerializerValidator(declaration),
            partial=True,
        )


def test_a_partial_spec_over_a_serializer_is_built() -> None:
    ServiceSpec(
        service=lambda data: data,
        permissions=[Unrestricted()],
        validator=SerializerValidator(_Note),
        partial=True,
    )


# --- under the kernel's dispatch --------------------------------------------------


@pytest.mark.django_db
def test_dispatch_hands_the_service_its_values_data_and_serializer() -> None:
    received: dict[str, Any] = {}

    def service(*, title: str, data: Any, serializer: Any) -> str:
        received.update(title=title, data=data, serializer=serializer)
        return title

    spec = ServiceSpec(
        service=service, permissions=[Unrestricted()], validator=SerializerValidator(_Note)
    )
    result = dispatch(spec, principal=None, arguments={"title": "hi"})
    assert result.value == "hi"
    assert received["data"] == {"title": "hi"}
    assert isinstance(received["serializer"], _Note)


@pytest.mark.django_db
def test_a_current_user_default_reads_the_principal_under_dispatch() -> None:
    received: dict[str, Any] = {}

    class S(serializers.Serializer):
        title = serializers.CharField()
        owner = serializers.HiddenField(default=serializers.CurrentUserDefault())

    def service(*, title: str, owner: Any) -> str:
        received.update(title=title, owner=owner)
        return title

    user = get_user_model().objects.create(username="ada")
    spec = ServiceSpec(
        service=service, permissions=[Unrestricted()], validator=SerializerValidator(S)
    )
    # REJECT, the default: the hidden field is no Parameter, and no argument
    # names it, so the closed argument set has nothing to refuse.
    dispatch(spec, principal=user, arguments={"title": "hi"})
    assert received == {"title": "hi", "owner": user}


@pytest.mark.django_db
def test_a_many_spec_validates_once_and_binds_one_list_serializer() -> None:
    received: dict[str, Any] = {}

    def service(*, data: Any, serializer: Any) -> str:
        received.update(data=data, serializer=serializer)
        return "ok"

    spec = ServiceSpec(
        service=service,
        permissions=[Unrestricted()],
        validator=SerializerValidator(_Note),
        many=True,
    )
    dispatch(spec, principal=None, arguments={"items": [{"title": "a"}, {"title": "b"}]})
    assert received["data"] == [{"title": "a"}, {"title": "b"}]
    # One bound serializer for the call, and it is DRF's list serializer, so a
    # service reading ``serializer.validated_data`` sees every row.
    assert isinstance(received["serializer"], serializers.ListSerializer)
    assert received["serializer"].validated_data == received["data"]


# --- validated_many ---------------------------------------------------------------


def test_validated_many_hands_the_rows_values_under_the_argument_and_binds_once() -> None:
    validated = SerializerValidator(_Note).validated_many(
        {0: {"title": "a"}, 3: {"title": "b", "count": 2}}, _context(), many_argument="items"
    )
    assert validated.values == {"items": [{"title": "a"}, {"title": "b", "count": 2}]}
    assert validated.data == [{"title": "a"}, {"title": "b", "count": 2}]
    assert set(validated.pool) == {"serializer"}
    assert isinstance(validated.pool["serializer"], serializers.ListSerializer)


def test_validated_many_keys_a_row_s_refusal_by_the_caller_s_index_not_the_position() -> None:
    # The shape check refused row 1, so the Validator is handed rows 0 and 2;
    # DRF counts the second as position 1. The caller's list is what the
    # error must name.
    with pytest.raises(InvalidArguments) as raised:
        SerializerValidator(_Note).validated_many(
            {0: {"title": "ok"}, 2: {"title": "too long"}}, _context(), many_argument="items"
        )
    assert raised.value.detail == {
        2: {"title": ["Ensure this field has no more than 5 characters."]}
    }
    assert isinstance(raised.value.detail[2]["title"][0], ErrorDetail)


def test_validated_many_reports_every_failing_row_at_once() -> None:
    with pytest.raises(InvalidArguments) as raised:
        SerializerValidator(_Note).validated_many(
            {0: {"title": "too long"}, 1: {"title": "ok"}, 2: {"title": "also too long"}},
            _context(),
            many_argument="items",
        )
    assert set(raised.value.detail) == {0, 2}


class _RefusesTheList(serializers.ListSerializer):
    def validate(self, attrs: list[Any]) -> list[Any]:
        if len(attrs) > 1:
            raise serializers.ValidationError("One row at most.")
        return attrs


class _Listed(serializers.Serializer):
    title = serializers.CharField()

    class Meta:
        list_serializer_class = _RefusesTheList


def test_a_list_level_refusal_sits_under_non_field_errors() -> None:
    # Only a call validated as a list can reach the list serializer's own
    # validate(): a per-row loop never builds one.
    with pytest.raises(InvalidArguments) as raised:
        SerializerValidator(_Listed).validated_many(
            {0: {"title": "a"}, 1: {"title": "b"}}, _context(), many_argument="items"
        )
    assert raised.value.detail == {"non_field_errors": ["One row at most."]}


class _OldShapedErrors(serializers.ListSerializer):
    """A list serializer reporting item errors as DRF below 3.18 does: a list, a blank per valid row."""

    @property
    def errors(self) -> Any:  # ty: ignore[invalid-method-override]
        return [{}, {"title": ["Bad."]}, {}, {"title": ["Worse."]}]


class _OldShaped(serializers.Serializer):
    title = serializers.CharField(max_length=1)

    class Meta:
        list_serializer_class = _OldShapedErrors


def test_item_errors_reported_as_a_list_are_keyed_by_the_callers_index_too() -> None:
    with pytest.raises(InvalidArguments) as raised:
        SerializerValidator(_OldShaped).validated_many(
            {0: {"title": "a"}, 4: {"title": "bb"}, 5: {"title": "c"}, 9: {"title": "dd"}},
            _context(),
            many_argument="items",
        )
    assert raised.value.detail == {4: {"title": ["Bad."]}, 9: {"title": ["Worse."]}}


def test_validated_many_over_a_dataclass_hands_each_row_s_instance() -> None:
    validated = SerializerValidator(_Row).validated_many(
        {0: {"title": "a"}, 1: {"title": "b", "qty": 3}}, _context(), many_argument="items"
    )
    assert validated.data == [_Row("a", 1), _Row("b", 3)]
    assert validated.values == {"items": [{"title": "a", "qty": 1}, {"title": "b", "qty": 3}]}


def test_a_partial_many_call_returns_only_what_each_row_sent() -> None:
    validated = SerializerValidator(_Note).validated_many(
        {0: {"count": 1}, 1: {"title": "b"}}, _context(partial=True), many_argument="items"
    )
    assert validated.data == [{"count": 1}, {"title": "b"}]


def test_a_partial_many_call_over_a_dataclass_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="partial"):
        SerializerValidator(_Row).validated_many(
            {0: {"title": "a"}}, _context(partial=True), many_argument="items"
        )


def test_validated_many_binds_no_instance_and_reads_the_principal() -> None:
    seen: dict[str, Any] = {}

    class S(serializers.Serializer):
        title = serializers.CharField()
        owner = serializers.HiddenField(default=serializers.CurrentUserDefault())

        def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
            seen["instance"] = self.instance
            return attrs

    principal = object()
    validated = SerializerValidator(S).validated_many(
        {0: {"title": "a"}},
        ValidationContext(principal, target=Post(), partial=False),
        many_argument="items",
    )
    assert validated.values["items"][0]["owner"] is principal
    assert seen["instance"] is None


@pytest.mark.django_db
def test_dispatch_reports_a_many_call_s_rows_under_the_argument_beside_the_shape_check() -> None:
    spec = ServiceSpec(
        service=lambda data: data,
        permissions=[Unrestricted()],
        validator=SerializerValidator(_Note),
        many=True,
    )
    with pytest.raises(KernelInvalidArguments) as raised:
        dispatch(
            spec,
            principal=None,
            arguments={"items": [{"title": "ok"}, {"title": "too long"}, {"count": "x"}]},
        )
    # The kernel's own class raises out of dispatch; the edge translates it.
    rows = raised.value.detail["items"]
    assert set(rows) == {1, 2}
    assert rows[1] == {"title": ["Ensure this field has no more than 5 characters."]}


# --- the refusal is DRF's too ------------------------------------------------------


def test_a_refusal_is_a_drf_validation_error_as_well() -> None:
    with pytest.raises(ValidationError) as single:
        SerializerValidator(_Note).validate({"title": "too long"}, _context())
    with pytest.raises(ValidationError) as many:
        SerializerValidator(_Note).validated_many(
            {0: {"title": "too long"}}, _context(), many_argument="items"
        )
    for raised in (single.value, many.value):
        assert isinstance(raised, InvalidArguments)
        assert raised.status_code == 400


# --- ambient: the registered request and view --------------------------------------


class _Spy(serializers.Serializer):
    title = serializers.CharField()
    seen: ClassVar[dict[str, Any]] = {}

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        type(self).seen.clear()
        type(self).seen.update(self.context)
        return attrs


def _ambient(**seeds: Any) -> ValidationContext:
    return ValidationContext(object(), ambient=MappingProxyType(seeds))


def test_the_registered_request_and_view_reach_the_serializer_context() -> None:
    principal = object()
    offline = build_offline_context(principal, {})
    context = ValidationContext(
        principal, ambient=MappingProxyType({"request": offline.request, "view": offline.view})
    )
    SerializerValidator(_Spy).validate({"title": "hi"}, context)
    assert _Spy.seen["request"] is offline.request
    assert _Spy.seen["view"] is offline.view
    assert _Spy.seen["format"] is None


def test_a_registered_request_alone_is_kept_beside_no_view() -> None:
    offline = build_offline_context(object(), {})
    SerializerValidator(_Spy).validate({"title": "hi"}, _ambient(request=offline.request))
    assert _Spy.seen["request"] is offline.request
    assert _Spy.seen["view"] is None


def test_a_registered_view_alone_answers_for_the_serializer_context() -> None:
    class View:
        def get_serializer_context(self) -> dict[str, Any]:
            return {"request": None, "format": None, "view": self, "extra": 1}

    view = View()
    SerializerValidator(_Spy).validate({"title": "hi"}, _ambient(view=view))
    assert _Spy.seen["view"] is view
    assert _Spy.seen["extra"] == 1


def test_a_registered_request_s_user_is_what_a_current_user_default_reads() -> None:
    class S(serializers.Serializer):
        owner = serializers.HiddenField(default=serializers.CurrentUserDefault())

    principal = object()
    offline = build_offline_context(principal, {})
    context = ValidationContext(principal, ambient=MappingProxyType({"request": offline.request}))
    assert SerializerValidator(S).validate({}, context) == {"owner": principal}


@pytest.mark.django_db
def test_dispatch_carries_a_registered_request_and_view_into_the_serializer_context() -> None:
    user = get_user_model().objects.create(username="grace")
    offline = build_offline_context(user, {"title": "hi"})
    seeds = DEFAULT_POOL_SEEDS.extend(request=lambda: offline.request, view=lambda: offline.view)
    spec = ServiceSpec(
        service=lambda title: title,
        permissions=[Unrestricted()],
        validator=SerializerValidator(_Spy),
    )
    dispatch(spec, principal=user, arguments={"title": "hi"}, pool_seeds=seeds)
    assert _Spy.seen["request"] is offline.request
    assert _Spy.seen["view"] is offline.view
