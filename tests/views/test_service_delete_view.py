"""Tests for ServiceDeleteView."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework.test import APIRequestFactory

from rest_framework_services import ServiceDeleteView, ServiceSpec
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


def _delete_author(*, instance: Author) -> None:
    instance.delete()


class _DeleteAuthorView(ServiceDeleteView):
    queryset = Author.objects.all()
    spec = ServiceSpec(service=_delete_author)


factory = APIRequestFactory()


@pytest.mark.django_db
class TestServiceDeleteView:
    def test_returns_204_by_default(self) -> None:
        author = Author.objects.create(name="x")
        request = factory.delete("/")
        response = _DeleteAuthorView.as_view()(request, pk=author.pk)
        assert response.status_code == 204
        assert not Author.objects.exists()

    def test_404_when_missing(self) -> None:
        request = factory.delete("/")
        response = _DeleteAuthorView.as_view()(request, pk=999)
        assert response.status_code == 404

    def test_with_input_serializer(self) -> None:
        @dataclass
        class _Reason:
            reason: str

        captured: dict[str, Any] = {}

        def fn(*, instance: Author, data: _Reason) -> None:
            captured["reason"] = data.reason
            instance.delete()

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(service=fn, input_serializer=_Reason)

        author = Author.objects.create(name="x")
        request = factory.delete("/", {"reason": "spam"}, format="json")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 204
        assert captured["reason"] == "spam"

    def test_with_output_serializer_returns_body(self) -> None:
        def fn(*, instance: Author) -> Author:
            instance.delete()
            return instance

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(service=fn, output_serializer=AuthorSerializer, success_status=200)

        author = Author.objects.create(name="bye")
        request = factory.delete("/")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data["name"] == "bye"

    def test_missing_spec_raises(self) -> None:
        class _Empty(ServiceDeleteView):
            queryset = Author.objects.all()

        author = Author.objects.create(name="x")
        request = factory.delete("/")
        with pytest.raises(NotImplementedError):
            _Empty.as_view()(request, pk=author.pk)

    def test_service_error_maps_to_422(self) -> None:
        from rest_framework_services import ServiceError

        def boom(*, instance: Author) -> None:
            raise ServiceError("nope")

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(service=boom, atomic=False)

        author = Author.objects.create(name="x")
        request = factory.delete("/")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 422

    def test_output_selector_used_when_set(self) -> None:
        def fn(*, instance: Author) -> Author:
            return instance

        def selector(*, result: Author) -> dict[str, Any]:
            return {"name_upper": result.name.upper()}

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=fn, output_selector=selector, success_status=200, atomic=False
            )

        author = Author.objects.create(name="bye")
        request = factory.delete("/")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data == {"name_upper": "BYE"}

    def test_returning_value_with_success_status_override(self) -> None:
        def fn(*, instance: Author) -> dict[str, Any]:
            return {"deleted": instance.pk}

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(service=fn, success_status=200, atomic=False)

        author = Author.objects.create(name="x")
        request = factory.delete("/")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data == {"deleted": author.pk}
