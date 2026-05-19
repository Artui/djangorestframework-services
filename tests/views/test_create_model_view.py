"""End-to-end integration test: a ``create_model`` factory wired into a
``ServiceCreateView`` and round-tripped over a real HTTP request.

Protects against view-layer regressions that would break the closure
shape (``is_async`` detection, kwargs filtering, response rendering).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ServiceCreateView,
    ServiceSpec,
    create_model,
)
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


@dataclass
class _CreateAuthorInput:
    name: str


class _CreateAuthorView(ServiceCreateView):
    spec = ServiceSpec(
        service=create_model(Author),
        input_serializer=_CreateAuthorInput,
        output_serializer=AuthorSerializer,
    )


@pytest.mark.django_db
class TestCreateModelFactoryRoundTrip:
    def test_post_returns_201_and_persists(self) -> None:
        factory = APIRequestFactory()
        request = factory.post("/authors/", {"name": "Ada"}, format="json")
        response = _CreateAuthorView.as_view()(request)
        assert response.status_code == 201
        response.render()
        data = response.data
        assert data is not None
        assert data["name"] == "Ada"
        assert Author.objects.filter(name="Ada").exists()
