"""A filter takes what its FilterSet reads, and is refused as the FilterSet refuses.

A CSV filter (``OrderingFilter``, ``BaseInFilter``) reads a string its widget
splits on commas, so ``"-title"`` and ``"-title,title"`` order a read as
``["-title"]`` does, off HTTP and over a live view, and an empty string orders
nothing. ``None`` for any filter narrows nothing. A refused value is answered
as the FilterSet answers it: a flat list under the filter's name.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import django_filters
import pytest
from asgiref.sync import async_to_sync
from django.http import QueryDict
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorListView,
    SelectorSpec,
    ServiceSpec,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from tests.testapp.models import Post


class _NumberIn(django_filters.BaseInFilter, django_filters.NumberFilter):
    pass


class _Posts(django_filters.FilterSet):
    numbers = _NumberIn(field_name="pk")
    ordering = django_filters.OrderingFilter(fields=(("title", "name"),))
    pks = django_filters.BaseInFilter(field_name="pk")
    titled = django_filters.CharFilter(field_name="title")
    kinds = django_filters.MultipleChoiceFilter(
        field_name="title", choices=[("a", "a"), ("b", "b"), ("c", "c")]
    )

    class Meta:
        model = Post
        fields: list[str] = []


class _Out(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ["title"]


_SPEC: SelectorSpec[Any, Any] = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=lambda: Post.objects.order_by("pk"),
    filter_set=_Posts,
    output_serializer=_Out,
    permission_classes=[],
)


def _sync(**kwargs: Any) -> DispatchResult:
    return dispatch_spec(_SPEC, user=None, **kwargs)


def _async(**kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(_SPEC, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


@pytest.fixture
def pks(db: Any) -> list[int]:
    return [Post.objects.create(title=title).pk for title in ("b", "a", "c")]


def _titles(result: DispatchResult) -> list[str]:
    return [post.title for post in result.value]


@_CORES
@pytest.mark.parametrize(
    ("sent", "titles"),
    [
        ("-name", ["c", "b", "a"]),
        (["-name"], ["c", "b", "a"]),
        ("-name,name", ["c", "b", "a"]),
        (["name,-name"], ["a", "b", "c"]),
        ("", ["b", "a", "c"]),
        (None, ["b", "a", "c"]),
    ],
    ids=["string", "list", "csv-string", "csv-in-a-list", "empty", "none"],
)
def test_an_ordering_takes_what_its_filter_set_reads(
    dispatch: Callable[..., DispatchResult], pks: list[int], sent: Any, titles: list[str]
) -> None:
    assert _titles(dispatch(params={"ordering": sent})) == titles


@_CORES
def test_filter_data_takes_it_too(dispatch: Callable[..., DispatchResult], pks: list[int]) -> None:
    assert _titles(dispatch(params={}, filter_data={"ordering": "-name"})) == ["c", "b", "a"]
    query = QueryDict("ordering=-name,name")
    assert _titles(dispatch(params={}, filter_data=query)) == ["c", "b", "a"]


@_CORES
def test_an_in_filter_takes_a_comma_separated_string(
    dispatch: Callable[..., DispatchResult], pks: list[int]
) -> None:
    sent = f"{pks[0]},{pks[1]}"

    assert _titles(dispatch(params={"pks": sent})) == ["b", "a"]


@_CORES
def test_a_number_in_filter_takes_numbers(
    dispatch: Callable[..., DispatchResult], pks: list[int]
) -> None:
    """A value that is no string is no widget's to split, and reaches the kernel as sent."""
    assert _titles(dispatch(params={"numbers": [pks[0], f"{pks[1]}"]})) == ["b", "a"]


@_CORES
def test_none_narrows_nothing(dispatch: Callable[..., DispatchResult], pks: list[int]) -> None:
    assert _titles(dispatch(params={"titled": None, "pks": None})) == ["b", "a", "c"]


@_CORES
@pytest.mark.parametrize("sent", ["bogus", ["bogus"]], ids=["string", "list"])
def test_an_unknown_ordering_is_refused_as_the_filter_set_refuses_it(
    dispatch: Callable[..., DispatchResult], pks: list[int], sent: Any
) -> None:
    with pytest.raises(InvalidArguments) as caught:
        dispatch(params={"ordering": sent})

    errors = caught.value.detail["ordering"]
    assert isinstance(errors, list)
    assert [str(error) for error in errors] == [
        "Select a valid choice. bogus is not one of the available choices."
    ]
    assert "Expected an array." not in str(caught.value.detail)


@_CORES
def test_a_multiple_choice_filter_still_refuses_a_bare_value(
    dispatch: Callable[..., DispatchResult], pks: list[int]
) -> None:
    """Only a CSV filter splits a string: a multiple choice filter never took one."""
    with pytest.raises(InvalidArguments):
        dispatch(params={"kinds": "a"})

    assert _titles(dispatch(params={"kinds": ["a", "b"]})) == ["b", "a"]


_view = type(
    "_View",
    (SelectorListView,),
    {"spec": _SPEC, "permission_classes": [], "authentication_classes": []},
).as_view()


@pytest.mark.parametrize(
    ("query", "titles"),
    [
        ("?ordering=-name", ["c", "b", "a"]),
        ("?ordering=-name,name", ["c", "b", "a"]),
        ("?ordering=", ["b", "a", "c"]),
    ],
)
def test_over_a_live_view_an_ordering_takes_its_widget_s_string(
    pks: list[int], query: str, titles: list[str]
) -> None:
    response = _view(APIRequestFactory().get("/" + query))

    assert response.status_code == 200, response.data
    assert [row["title"] for row in response.data] == titles


def test_over_a_live_view_an_in_filter_takes_its_widget_s_string(pks: list[int]) -> None:
    response = _view(APIRequestFactory().get(f"/?pks={pks[0]},{pks[1]}"))

    assert response.status_code == 200, response.data
    assert [row["title"] for row in response.data] == ["b", "a"]


def test_over_a_live_view_an_unknown_ordering_is_a_flat_refusal(pks: list[int]) -> None:
    response = _view(APIRequestFactory().get("/?ordering=bogus"))

    assert response.status_code == 400
    assert [str(error) for error in response.data["ordering"]] == [
        "Select a valid choice. bogus is not one of the available choices."
    ]


@pytest.mark.parametrize(
    "call", [dispatch_spec, async_to_sync(adispatch_spec)], ids=["sync", "async"]
)
def test_a_selector_s_own_list_parameter_keeps_its_refusal_at_the_index(
    call: Callable[..., DispatchResult], pks: list[int]
) -> None:
    """Only a filter's refusal is flattened: a parameter of the selector's own is the kernel's."""

    def posts(ids: list[int] | None = None) -> Any:
        return Post.objects.filter(pk__in=ids or [])

    spec = SelectorSpec(
        kind=SelectorKind.LIST, selector=posts, filter_set=_Posts, permission_classes=[]
    )

    with pytest.raises(InvalidArguments) as caught:
        call(spec, user=None, params={"ids": ["x"]})

    assert set(caught.value.detail["ids"]) == {0}


def _a_post_by() -> ServiceSpec[Any, Any, Any]:
    def a_post(pk: int) -> Any:
        return Post.objects.filter(pk=pk)

    def title(*, instance: Post) -> str:
        return instance.title

    return ServiceSpec(
        service=title,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=a_post, filter_set=_Posts
        ),
        permission_classes=[],
        atomic=False,
    )


@pytest.mark.parametrize(
    "call", [dispatch_spec, async_to_sync(adispatch_spec)], ids=["sync", "async"]
)
def test_a_target_lookup_s_filter_takes_what_its_filter_set_reads(
    call: Callable[..., DispatchResult], pks: list[int]
) -> None:
    sent = {"pk": pks[1], "pks": f"{pks[0]},{pks[1]}", "titled": None}

    assert call(_a_post_by(), user=None, params=sent).value == "a"


@pytest.mark.parametrize(
    "call", [dispatch_spec, async_to_sync(adispatch_spec)], ids=["sync", "async"]
)
def test_a_target_lookup_s_filter_is_refused_as_its_filter_set_refuses_it(
    call: Callable[..., DispatchResult], pks: list[int]
) -> None:
    with pytest.raises(InvalidArguments) as caught:
        call(_a_post_by(), user=None, params={"pk": pks[1], "ordering": ["bogus"]})

    assert [str(error) for error in caught.value.detail["ordering"]] == [
        "Select a valid choice. bogus is not one of the available choices."
    ]
