"""Tests for ServiceUpdateView."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework.test import APIRequestFactory

from rest_framework_services import ServiceUpdateView
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


@dataclass
class _UpdateAuthorInput:
    name: str


def _update_author(*, instance: Author, data: _UpdateAuthorInput) -> Author:
    instance.name = data.name
    instance.save(update_fields=["name"])
    return instance


class _UpdateAuthorView(ServiceUpdateView):
    queryset = Author.objects.all()
    service = _update_author
    input_serializer = _UpdateAuthorInput
    output_serializer = AuthorSerializer


factory = APIRequestFactory()


@pytest.mark.django_db
class TestServiceUpdateView:
    def test_put_full_update(self) -> None:
        author = Author.objects.create(name="orig")
        request = factory.put("/", {"name": "new"}, format="json")
        response = _UpdateAuthorView.as_view()(request, pk=author.pk)
        author.refresh_from_db()
        assert response.status_code == 200
        assert author.name == "new"

    def test_patch_partial_update(self) -> None:
        author = Author.objects.create(name="orig")
        request = factory.patch("/", {"name": "new"}, format="json")
        response = _UpdateAuthorView.as_view()(request, pk=author.pk)
        author.refresh_from_db()
        assert response.status_code == 200
        assert author.name == "new"

    def test_missing_service_raises(self) -> None:
        class _Empty(ServiceUpdateView):
            queryset = Author.objects.all()

        author = Author.objects.create(name="x")
        request = factory.put("/", {"name": "y"}, format="json")
        with pytest.raises(NotImplementedError):
            _Empty.as_view()(request, pk=author.pk)

    def test_404_when_instance_missing(self) -> None:
        request = factory.put("/", {"name": "x"}, format="json")
        response = _UpdateAuthorView.as_view()(request, pk=999)
        assert response.status_code == 404

    def test_get_object_overridable(self) -> None:
        author = Author.objects.create(name="orig")

        class _Custom(ServiceUpdateView):
            service = _update_author
            input_serializer = _UpdateAuthorInput
            output_serializer = AuthorSerializer

            def get_object(self) -> Any:
                return author

        request = factory.put("/", {"name": "z"}, format="json")
        response = _Custom.as_view()(request)
        author.refresh_from_db()
        assert response.status_code == 200
        assert author.name == "z"

    def test_output_selector(self) -> None:
        author = Author.objects.create(name="orig")

        def selector(*, instance: Author) -> dict[str, Any]:
            return {"latest_name": instance.name}

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            service = _update_author
            input_serializer = _UpdateAuthorInput
            output_selector = selector

        request = factory.patch("/", {"name": "fresh"}, format="json")
        response = _View.as_view()(request, pk=author.pk)
        assert response.data == {"latest_name": "fresh"}

    def test_service_error_maps_to_422(self) -> None:
        from rest_framework_services import ServiceError

        def boom(*, instance: Author, data: _UpdateAuthorInput) -> None:
            raise ServiceError("nope")

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            service = boom
            input_serializer = _UpdateAuthorInput
            atomic = False

        author = Author.objects.create(name="x")
        request = factory.patch("/", {"name": "y"}, format="json")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 422

    def test_no_input_serializer(self) -> None:
        captured: dict[str, Any] = {}

        def fn(*, instance: Author) -> Author:
            captured["pk"] = instance.pk
            return instance

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            service = fn
            output_serializer = AuthorSerializer

        author = Author.objects.create(name="hi")
        request = factory.put("/", {}, format="json")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 200
        assert captured["pk"] == author.pk

    def test_output_selector_returning_none_renders_204(self) -> None:
        def fn(*, instance: Author, data: _UpdateAuthorInput) -> Author:
            return instance

        def selector(*, result: Author) -> None:
            return None

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            service = fn
            input_serializer = _UpdateAuthorInput
            output_selector = selector

        author = Author.objects.create(name="x")
        request = factory.patch("/", {"name": "y"}, format="json")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 204

    def test_falls_back_to_instance_when_service_returns_none(self) -> None:
        """When output_selector is missing AND service returns None, render
        the in-memory instance (which the service mutated in place)."""
        author = Author.objects.create(name="orig")

        def void_update(*, instance: Author, data: _UpdateAuthorInput) -> None:
            instance.name = data.name
            instance.save(update_fields=["name"])
            # Returns None on purpose.

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            service = void_update
            input_serializer = _UpdateAuthorInput
            output_serializer = AuthorSerializer

        request = factory.patch("/", {"name": "renamed"}, format="json")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data["name"] == "renamed"
