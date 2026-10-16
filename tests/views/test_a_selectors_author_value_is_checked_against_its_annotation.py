"""A selector parameter's value from a view hook, a provider or a route capture is checked.

Over a live ``SelectorListView``, the value ``get_selector_kwargs()``, the
spec's ``kwargs`` provider or a route capture gives a selector parameter is
checked against the parameter's annotation, as a value a caller sends is: a
``None`` the annotation does not admit and a value that does not convert are
refused with a 400 before the selector runs, where 0.57.0 handed the selector
the value as given. A string that converts reaches it as the annotated type.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import SelectorKind, SelectorListView, SelectorSpec
from tests.testapp.models import Post

_factory = APIRequestFactory()
_SEEN: list[Any] = []


class _PostOut(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ("id",)


def _selector(*, x: int = 0) -> Any:
    _SEEN.append(x)
    return Post.objects.none()


def _respond(channel: str, value: Any) -> Any:
    """The response of a live list view whose selector's ``x`` comes from ``channel``."""
    spec_kwargs: dict[str, Any] = {}
    attrs: dict[str, Any] = {}
    route: dict[str, Any] = {}
    if channel == "provider":
        spec_kwargs["kwargs"] = lambda **_: {"x": value}
    elif channel == "hook":
        attrs["get_selector_kwargs"] = lambda self: {"x": value}
    else:
        route["x"] = value
    spec = SelectorSpec(
        kind=SelectorKind.LIST, selector=_selector, output_serializer=_PostOut, **spec_kwargs
    )
    view = type("View", (SelectorListView,), {"spec": spec, **attrs}).as_view()
    _SEEN.clear()
    return view(_factory.get("/"), **route)


_CHANNELS = ["hook", "provider", "route"]


@pytest.mark.django_db
@pytest.mark.parametrize("channel", _CHANNELS)
def test_a_none_the_annotation_does_not_admit_is_refused(channel: str) -> None:
    response = _respond(channel, None)

    assert response.status_code == 400
    assert response.data == {"x": ["This field cannot be null."]}
    assert response.data["x"][0].code == "null"
    assert _SEEN == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("channel", "message"),
    [
        ("hook", "Expected an integer."),
        ("provider", "Expected an integer."),
        # A route capture is read as the route's text, so the kernel's coercion
        # of a string answers it.
        ("route", "Enter a whole number."),
    ],
)
def test_a_value_that_does_not_convert_is_refused(channel: str, message: str) -> None:
    response = _respond(channel, "abc")

    assert response.status_code == 400
    assert response.data == {"x": [message]}
    assert _SEEN == []


@pytest.mark.django_db
@pytest.mark.parametrize("channel", _CHANNELS)
def test_a_string_that_converts_reaches_the_selector_as_the_annotated_type(channel: str) -> None:
    response = _respond(channel, "7")

    assert response.status_code == 200
    assert _SEEN == [7]
