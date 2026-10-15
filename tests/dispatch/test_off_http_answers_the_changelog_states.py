"""Answers that differ from 0.57.0 and that the CHANGELOG states.

Each test pins one stated difference, so the sentence describing it cannot
outlive the behaviour: a nullability read off the annotation rather than the
default, the row bound of a list serializer that cannot be built without a
request, the kernel's typed reading of filter values (over a live view too,
for a provider's value), and ``REJECT``'s answer beside the serializer's.
"""

from __future__ import annotations

from typing import Any

import django_filters
import pytest
from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ArgumentBinding,
    SelectorKind,
    SelectorListView,
    SelectorSpec,
    ServiceSpec,
    UnknownArguments,
    dispatch_spec,
)
from tests.testapp.models import Post

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
