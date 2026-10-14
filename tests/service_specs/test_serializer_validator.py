"""Tests for ``SerializerValidator``."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django_service_specs.authorization.unrestricted import Unrestricted
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.parameters.invalid_arguments import InvalidArguments
from django_service_specs.parameters.parameters import Parameter, Parameters
from django_service_specs.specs.service_spec import ServiceSpec
from django_service_specs.validation.validation_context import ValidationContext
from django_service_specs.validation.validator import Validator
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail
from rest_framework.request import Request
from rest_framework.validators import UniqueValidator
from rest_framework_dataclasses.serializers import DataclassSerializer

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


@pytest.mark.django_db
def test_a_partial_spec_over_a_dataclass_is_built_and_refused_on_its_first_call() -> None:
    # supports_partial is a ClassVar on the kernel's Validator, so it cannot
    # answer for one declaration; the refusal comes at the call instead.
    spec = ServiceSpec(
        service=lambda data: data,
        permissions=[Unrestricted()],
        validator=SerializerValidator(_MoveInSerializer),
        partial=True,
    )
    with pytest.raises(ImproperlyConfigured, match="partial"):
        dispatch(spec, principal=None, arguments={"event_id": 1})


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
def test_a_many_spec_refuses_the_bound_serializer() -> None:
    # The kernel validates a many spec row by row and calls its service once,
    # so a value bound for one row has nowhere to go. Pinned here so the
    # compilation sees the conflict rather than finding it on a first call.
    spec = ServiceSpec(
        service=lambda data: data,
        permissions=[Unrestricted()],
        validator=SerializerValidator(_Note),
        many=True,
    )
    with pytest.raises(ImproperlyConfigured, match="serializer"):
        dispatch(spec, principal=None, arguments={"items": [{"title": "hi"}]})
