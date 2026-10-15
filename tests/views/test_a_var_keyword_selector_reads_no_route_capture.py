"""A selector taking a bare ``**kwargs`` is handed no route capture it does not declare.

Its keywords are the declared arguments and the seeds, so a capture no
callable declares never reaches it, and a retrieve scoped by that capture
finds no row: a ``404`` with nothing naming the capture, where 0.57.0 handed
the capture on and answered the row. Declaring the capture, as a keyword
parameter or a ``UrlKwarg`` read, hands it on again.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    SelectorKind,
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


def _view(**spec: Any) -> Any:
    class _Retrieve(SelectorRetrieveView):
        permission_classes: Any = []

    _Retrieve.spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_by_kwargs, output_serializer=_PostOut, **spec
    )
    return _Retrieve.as_view()


def test_an_undeclared_capture_never_reaches_it() -> None:
    post = Post.objects.create(title="p")

    response = _view()(_factory.get("/"), pk=post.pk)

    assert response.status_code == 404


def test_a_declared_capture_reaches_it() -> None:
    post = Post.objects.create(title="p")

    response = _view(reads=[UrlKwarg("pk", "integer")])(_factory.get("/"), pk=post.pk)

    assert response.status_code == 200, response.data
    assert response.data == {"id": post.pk, "title": "p"}
