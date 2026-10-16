"""A selector taking a bare ``**kwargs`` is handed no route capture it does not declare.

Its keywords are the declared arguments and the seeds, so a capture no
callable declares never reaches it. A retrieve scoped by that capture finds no
row: a ``404`` with nothing naming the capture, where 0.57.0 handed the capture
on and answered the row. A list selector fails the same way and more quietly,
with a ``200`` and no error: one that filters only when the capture is present
answers every row, and one reading it with ``.get`` answers none. Declaring the
capture, as a keyword parameter or a ``UrlKwarg`` read, hands it on again, and
its ``type`` has to be the one the route's converter yields: a string-typed
read refuses an ``<int:pk>`` capture.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.urls import path
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    SelectorKind,
    SelectorListView,
    SelectorRetrieveView,
    SelectorSpec,
    UrlKwarg,
)
from tests.testapp.models import Post

pytestmark = pytest.mark.django_db

_factory = APIRequestFactory()


class _PostOut(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ("id", "title")


def _by_kwargs(**kwargs: Any) -> Post | None:
    return Post.objects.filter(pk=kwargs.get("pk")).first()


def _filtered_when_present(**kwargs: Any) -> Any:
    """The shape the upgrade note warns about: scoping only when the capture is there."""
    queryset = Post.objects.order_by("id")
    if "pk" in kwargs:
        queryset = queryset.filter(pk=kwargs["pk"])
    return queryset


def _filtered_by_get(**kwargs: Any) -> Any:
    return Post.objects.filter(pk=kwargs.get("pk"))


def _view(**spec: Any) -> Any:
    class _Retrieve(SelectorRetrieveView):
        permission_classes: Any = []

    _Retrieve.spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_by_kwargs, output_serializer=_PostOut, **spec
    )
    return _Retrieve.as_view()


def _list_view(selector: Any, **spec: Any) -> Any:
    class _List(SelectorListView):
        permission_classes: Any = []

    _List.spec = SelectorSpec(
        kind=SelectorKind.LIST, selector=selector, output_serializer=_PostOut, **spec
    )
    return _List.as_view()


def _routed(route: str, view: Any, post: Post) -> Any:
    """Call ``view`` the way a URLconf does, so the converter decides the capture's type."""
    match = path(route, view).resolve(f"{post.pk}/")
    assert match is not None
    return match.func(_factory.get("/"), *match.args, **match.kwargs)


def test_an_undeclared_capture_never_reaches_it() -> None:
    post = Post.objects.create(title="p")

    response = _view()(_factory.get("/"), pk=post.pk)

    assert response.status_code == 404


def test_a_declared_capture_reaches_it() -> None:
    post = Post.objects.create(title="p")

    response = _view(reads=[UrlKwarg("pk", "integer")])(_factory.get("/"), pk=post.pk)

    assert response.status_code == 200, response.data
    assert response.data == {"id": post.pk, "title": "p"}


# --- a list selector answers without the capture, and says nothing ----------------


def test_a_list_that_filters_only_when_the_capture_is_present_answers_every_row() -> None:
    post = Post.objects.create(title="p")
    other = Post.objects.create(title="other")

    response = _routed("<int:pk>/", _list_view(_filtered_when_present), post)

    assert response.status_code == 200, response.data
    assert [row["id"] for row in response.data] == [post.pk, other.pk]


def test_a_list_reading_the_capture_with_get_answers_no_row() -> None:
    post = Post.objects.create(title="p")
    Post.objects.create(title="other")

    response = _routed("<int:pk>/", _list_view(_filtered_by_get), post)

    assert response.status_code == 200, response.data
    assert response.data == []


# --- the remedy: declare the capture with the type the converter yields -----------


@pytest.mark.parametrize(
    ("route", "type_"),
    [("<int:pk>/", "integer"), ("<str:pk>/", "string")],
    ids=["an-int-route-read-as-an-integer", "a-string-route-read-as-a-string"],
)
@pytest.mark.parametrize("selector", [_filtered_when_present, _filtered_by_get])
def test_a_list_scoped_by_a_declared_capture_answers_its_rows(
    route: str, type_: str, selector: Any
) -> None:
    post = Post.objects.create(title="p")
    Post.objects.create(title="other")

    response = _routed(route, _list_view(selector, reads=[UrlKwarg("pk", type=type_)]), post)

    assert response.status_code == 200, response.data
    assert [row["id"] for row in response.data] == [post.pk]


@pytest.mark.parametrize(
    ("route", "type_"),
    [("<int:pk>/", "integer"), ("<str:pk>/", "string")],
    ids=["an-int-route-read-as-an-integer", "a-string-route-read-as-a-string"],
)
def test_a_retrieve_scoped_by_a_declared_capture_answers_its_row(route: str, type_: str) -> None:
    post = Post.objects.create(title="p")

    response = _routed(route, _view(reads=[UrlKwarg("pk", type=type_)]), post)

    assert response.status_code == 200, response.data
    assert response.data == {"id": post.pk, "title": "p"}


def test_a_string_read_of_an_int_route_s_capture_is_refused() -> None:
    """``UrlKwarg("pk")`` is typed ``string``, which an ``<int:pk>`` capture is not."""
    post = Post.objects.create(title="p")

    response = _routed("<int:pk>/", _view(reads=[UrlKwarg("pk")]), post)

    assert response.status_code == 400
    assert response.data == {"pk": ["Expected a string."]}
