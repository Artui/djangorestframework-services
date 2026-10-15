"""Answers that differ from 0.57.0 and that the CHANGELOG states.

Each test pins one stated difference, so the sentence describing it cannot
outlive the behaviour: a nullability read off the annotation rather than the
default, a nested value of the wrong shape in the kernel's words, the row bound of a list serializer that cannot be built without a
request, the kernel's typed reading of filter values (over a live view too,
for a provider's value), and ``REJECT``'s answer beside the serializer's.
The last section holds each example the upgrade notes give.
"""

from __future__ import annotations

from typing import Annotated, Any

import django_filters
import pytest
from django.db import models
from django.db.models import QuerySet
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

import rest_framework_services
from rest_framework_services import (
    ArgumentBinding,
    ChildSpec,
    NotClientInput,
    SelectorKind,
    SelectorListView,
    SelectorSpec,
    ServiceSpec,
    UnknownArguments,
    apply_input,
    create_model,
    dispatch_spec,
)
from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from rest_framework_services.types.unset import UNSET
from rest_framework_services.types.view_hooks import ViewHooks
from tests.testapp.models import Catalog, Post, Section

# --- null follows the annotation, not the default ---------------------------------


def _int_defaulted_to_none(*, x: int = None) -> dict[str, Any]:  # ty: ignore[invalid-parameter-default]
    return {"x": x}


def _optional_int(*, x: int | None = None) -> dict[str, Any]:
    return {"x": x}


@pytest.mark.django_db
def test_a_none_default_does_not_admit_null() -> None:
    spec = ServiceSpec(service=_int_defaulted_to_none, atomic=False)

    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(
            spec,
            user=None,
            params={"x": None},
            argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        )

    assert excinfo.value.get_codes() == {"x": ["null"]}


@pytest.mark.django_db
def test_an_optional_annotation_admits_null() -> None:
    spec = ServiceSpec(service=_optional_int, atomic=False)

    result = dispatch_spec(
        spec, user=None, params={"x": None}, argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS
    )

    assert result.service_result == {"x": None}


# --- a list serializer that cannot be built without a request -----------------------


class _OrderLine(serializers.Serializer):
    sku = serializers.CharField()


class _Order(serializers.Serializer):
    lines = _OrderLine(many=True)
    address = _OrderLine()


def _take_order(*, data: Any) -> Any:
    return data


@pytest.mark.parametrize(
    ("sent", "refusal"),
    [
        (
            {"lines": [1], "address": {"sku": "a"}},
            {"lines": {0: {"non_field_errors": [("Expected an object.", "invalid")]}}},
        ),
        (
            {"lines": {"sku": "a"}, "address": {"sku": "a"}},
            {"lines": [("Expected an array.", "not_a_list")]},
        ),
        (
            {"lines": [{"sku": "a"}], "address": 5},
            {"address": [("Expected an object.", "invalid")]},
        ),
    ],
    ids=["nested-row-no-object", "nested-list-no-list", "nested-object-a-number"],
)
def test_a_nested_value_of_the_wrong_shape_is_refused_in_the_kernels_words(
    sent: dict[str, Any], refusal: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError) as caught:
        dispatch_spec(
            ServiceSpec(service=_take_order, input_serializer=_Order), user=None, params=sent
        )

    assert _worded(caught.value.detail) == refusal


def _worded(detail: Any) -> Any:
    """Each message with its code, so the pin holds both."""
    if isinstance(detail, dict):
        return {key: _worded(value) for key, value in detail.items()}
    if isinstance(detail, list):
        return [_worded(item) for item in detail]
    return (str(detail), detail.code)


class _Rows(serializers.ListSerializer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["max_length"] = 1500
        super().__init__(*args, **kwargs)


class _RowNeedingARequest(serializers.Serializer):
    n = serializers.IntegerField()

    class Meta:
        list_serializer_class = _Rows

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _ = self.context["request"].method


def _count(*, data: list[Any]) -> int:
    return len(data)


@pytest.mark.django_db
def test_an_unbuildable_list_serializer_is_held_to_the_default_bound() -> None:
    """Its own ``max_length`` of 1500 cannot be read, so 1000 rows is the most."""
    spec = ServiceSpec(
        service=_count, input_serializer=_RowNeedingARequest, many=True, atomic=False
    )

    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(spec, user=None, params=[{"n": 1}] * 1200)

    assert excinfo.value.get_codes() == {"non_field_errors": ["max_length"]}


# --- filter values are read by the kernel's types first -----------------------------


class _PostFilter(django_filters.FilterSet):
    ids = django_filters.BaseInFilter(field_name="id")
    posts = django_filters.ModelMultipleChoiceFilter(field_name="id", queryset=Post.objects.all())
    title = django_filters.CharFilter()

    class Meta:
        model = Post
        fields: list[str] = []


def _all_posts() -> QuerySet[Post]:
    return Post.objects.order_by("title")


_LISTED = SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts, filter_set=_PostFilter)


@pytest.mark.django_db
def test_a_single_value_for_a_multiple_choice_filter_is_expected_as_an_array() -> None:
    post = Post.objects.create(title="a")

    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(_LISTED, user=None, params={"posts": post.pk})

    assert excinfo.value.detail == {"posts": ["Expected an array."]}
    assert excinfo.value.get_codes() == {"posts": ["not_a_list"]}


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["ids", "posts"])
def test_a_number_in_a_filters_list_is_expected_as_a_string(name: str) -> None:
    post = Post.objects.create(title="a")

    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(_LISTED, user=None, params={name: [post.pk]})

    assert "Expected a string." in str(excinfo.value.detail)


_SEEN: dict[str, Any] = {}


def _titled(*, title: str | None = None) -> QuerySet[Post]:
    _SEEN["title"] = title
    return Post.objects.order_by("title")


@pytest.mark.django_db
def test_a_providers_value_for_a_filters_name_filters_the_set() -> None:
    Post.objects.create(title="a")
    Post.objects.create(title="b")
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_titled,
        filter_set=_PostFilter,
        kwargs=lambda: {"title": "b"},
    )

    result = dispatch_spec(spec, user=None, params={"title": "a"})

    assert [post.title for post in result.value] == ["b"]
    assert _SEEN == {"title": "b"}


@pytest.mark.django_db
def test_a_providers_value_for_a_filters_name_filters_a_live_views_set() -> None:
    Post.objects.create(title="a")
    Post.objects.create(title="b")

    class _TitleOut(serializers.Serializer):
        title = serializers.CharField()

    class _View(SelectorListView):
        permission_classes = []
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_titled,
            filter_set=_PostFilter,
            kwargs=lambda: {"title": "b"},
            output_serializer=_TitleOut,
        )

    response = _View.as_view()(APIRequestFactory().get("/", {"title": "a"}))

    assert response.status_code == 200
    assert [row["title"] for row in response.data] == ["b"]


@pytest.mark.django_db
def test_a_primary_key_no_row_has_is_an_invalid_choice() -> None:
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(_LISTED, user=None, params={"posts": ["99"]})

    assert excinfo.value.get_codes() == {"posts": ["invalid_choice"]}


# --- REJECT answers an undeclared key beside the serializer's refusals --------------


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


def _created(*, data: Any) -> Any:
    return data


@pytest.mark.django_db
def test_under_reject_an_undeclared_key_is_refused_beside_a_missing_field() -> None:
    spec = ServiceSpec(service=_created, input_serializer=_TitleIn, atomic=False)

    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(
            spec, user=None, params={"bogus": 1}, unknown_arguments=UnknownArguments.REJECT
        )

    assert excinfo.value.get_codes() == {"title": ["required"], "non_field_errors": ["invalid"]}


# --- the upgrade notes' examples ---------------------------------------------------


class _Line(serializers.Serializer):
    title = serializers.CharField(max_length=5)


def _lines(*, data: Any) -> Any:
    return data


_BULK = ServiceSpec(service=_lines, input_serializer=_Line, many=True, atomic=False)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("params", "kwargs"),
    [
        ({"title": "a"}, {}),
        ([{"title": "a"}, {"title": "toolong"}], {}),
        ([{"title": "a", "bogus": 1}], {"unknown_arguments": UnknownArguments.REJECT}),
        ([{"title": "a"}] * 1001, {}),
    ],
    ids=["no-list", "a-row-refused", "an-undeclared-key", "past-the-bound"],
)
def test_bare_rows_are_refused_with_drfs_validation_error_not_invalid_arguments(
    params: Any, kwargs: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(_BULK, user=None, params=params, **kwargs)

    assert type(excinfo.value) is ValidationError
    assert not isinstance(excinfo.value, InvalidArguments)


@pytest.mark.django_db
def test_a_refused_argument_is_invalid_arguments_a_drf_validation_error() -> None:
    with pytest.raises(InvalidArguments) as excinfo:
        dispatch_spec(
            _BULK, user=None, params={"items": [{"title": "toolong"}]}, many_as_argument=True
        )

    assert isinstance(excinfo.value, ValidationError)
    assert "InvalidArguments" not in rest_framework_services.__all__


def _no_serializer(*, data: Any = "default", title: str = "default") -> dict[str, Any]:
    return {"data": data, "title": title}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("binding", "title"),
    [
        (ArgumentBinding.AUTO, "default"),
        (ArgumentBinding.BUNDLE, "default"),
        (ArgumentBinding.SPREAD_AUTHOR_WINS, "c"),
    ],
)
def test_a_serializer_less_service_is_handed_an_empty_data(
    binding: ArgumentBinding, title: str
) -> None:
    result = dispatch_spec(
        ServiceSpec(service=_no_serializer, atomic=False),
        user=None,
        params={"title": "c"},
        argument_binding=binding,
    )

    assert result.value == {"data": {}, "title": title}


@pytest.mark.django_db
def test_under_reject_a_bare_rows_undeclared_key_is_refused_at_that_row() -> None:
    with pytest.raises(ValidationError) as excinfo:
        dispatch_spec(
            _BULK,
            user=None,
            params=[{"title": "a"}, {"title": "b", "bogus": 1}],
            unknown_arguments=UnknownArguments.REJECT,
        )

    assert excinfo.value.detail == [{}, {"non_field_errors": ["Unexpected argument(s): 'bogus'."]}]


def _titled_service(*, data: Any, title: str = "default") -> dict[str, Any]:
    return {"data": dict(data), "title": title}


def _server_title() -> dict[str, Any]:
    return {"title": "srv"}


@pytest.mark.django_db
@pytest.mark.parametrize("source", ["hook", "provider"])
def test_input_data_outranks_a_hook_or_provider_for_the_parameter(source: str) -> None:
    extra: dict[str, Any] = {}
    if source == "hook":
        extra["view_hooks"] = ViewHooks(extra_kwargs={"title": "hook"})
    spec = ServiceSpec(
        service=_titled_service,
        input_serializer=_TitleIn,
        input_data=_server_title,
        kwargs=(lambda: {"title": "provider"}) if source == "provider" else None,
        atomic=False,
    )

    result = dispatch_spec(spec, user=None, params={"title": "client"}, **extra)

    assert result.value == {"data": {"title": "srv"}, "title": "srv"}


def _hidden_title(
    *, data: Any, title: Annotated[str, NotClientInput] = "default"
) -> dict[str, Any]:
    return {"title": title}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("service", "title"), [(_titled_service, "client"), (_hidden_title, "default")]
)
def test_a_hooks_unset_declines_the_name(service: Any, title: str) -> None:
    spec = ServiceSpec(service=service, input_serializer=_TitleIn, atomic=False)

    result = dispatch_spec(
        spec,
        user=None,
        params={"title": "client"},
        view_hooks=ViewHooks(extra_kwargs={"title": UNSET}),
    )

    assert result.value["title"] == title


@pytest.mark.django_db
def test_a_none_relation_row_is_refused_at_its_index() -> None:
    write = create_model(Catalog, relations={"sections": ChildSpec(model=Section, fk="catalog")})

    # Called directly, so the kernel's own class, which the edge translates.
    with pytest.raises(KernelInvalidArguments) as excinfo:
        write(data={"name": "c", "sections": [None]})

    assert excinfo.value.detail == {
        "sections": {0: {"non_field_errors": ["This field cannot be null."]}}
    }


class _Upload(models.Model):
    upload = models.FileField(upload_to="uploads", blank=True)

    class Meta:
        app_label = "testapp"
        managed = False


@pytest.mark.parametrize(
    ("value", "stored"), [(None, "kept.txt"), (False, "")], ids=["none", "false"]
)
def test_a_file_field_keeps_its_file_for_none_and_clears_it_for_false(
    value: Any, stored: str
) -> None:
    row = _Upload(upload="kept.txt")

    apply_input(row, {"upload": value})

    assert row.upload.name == stored
