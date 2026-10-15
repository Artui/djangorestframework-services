"""A view hook's value for a ``ModelSerializer`` field is validated as the field validates it.

What the changelog's upgrade note tells a project to expect: a related field
takes a primary key, not the row, and hands the service the row; a value its
validators refuse is a 400 for the client; and a read-only field validates
nothing, so a hook's value for it reaches the service parameter as given.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import ServiceCreateView, ServiceSpec
from tests.testapp.models import Author, Post

_factory = APIRequestFactory()


class _PostIn(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ["title", "author", "views"]
        read_only_fields = ["views"]
        extra_kwargs = {"title": {"max_length": 3}}


def _view(seen: dict[str, Any], hook_values: dict[str, Any]) -> Any:
    def service(*, data: Any, author: Any = None, views: Any = None, title: Any = None) -> None:
        seen.update(data=dict(data), author=author, views=views, title=title)

    class _View(ServiceCreateView):
        permission_classes = []
        spec = ServiceSpec(service=service, input_serializer=_PostIn, atomic=False)

        def get_service_kwargs(self) -> dict[str, Any]:
            return hook_values

    return _View


@pytest.mark.django_db
def test_a_row_for_a_related_field_is_refused() -> None:
    author = Author.objects.create(name="a")
    seen: dict[str, Any] = {}

    response = _view(seen, {"author": author}).as_view()(
        _factory.post("/", {"title": "t"}, format="json")
    )

    assert response.status_code == 400
    assert response.data == {"author": ["Incorrect type. Expected pk value, received Author."]}
    assert seen == {}


@pytest.mark.django_db
def test_a_primary_key_for_a_related_field_arrives_as_its_row() -> None:
    author = Author.objects.create(name="a")
    seen: dict[str, Any] = {}

    response = _view(seen, {"author": author.pk}).as_view()(
        _factory.post("/", {"title": "t"}, format="json")
    )

    assert response.status_code == 204, response.data
    assert seen["author"] == author
    assert seen["data"] == {"title": "t", "author": author}


@pytest.mark.django_db
def test_a_value_the_fields_validators_refuse_is_the_clients_400() -> None:
    seen: dict[str, Any] = {}

    response = _view(seen, {"title": "too long"}).as_view()(
        _factory.post("/", {"title": "ok"}, format="json")
    )

    assert response.status_code == 400
    assert response.data == {"title": ["Ensure this field has no more than 3 characters."]}
    assert seen == {}


@pytest.mark.django_db
def test_a_value_for_a_read_only_field_reaches_the_parameter_as_given() -> None:
    seen: dict[str, Any] = {}

    response = _view(seen, {"views": "42"}).as_view()(
        _factory.post("/", {"title": "t"}, format="json")
    )

    assert response.status_code == 204, response.data
    assert seen["views"] == "42"
    assert seen["data"] == {"title": "t"}
