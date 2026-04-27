"""Tests for ServiceCreateView."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework.test import APIRequestFactory

from rest_framework_services import ServiceCreateView, ServiceError, ServiceValidationError
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


@dataclass
class _CreateAuthorInput:
    name: str


def _create_author(*, data: _CreateAuthorInput) -> Author:
    return Author.objects.create(name=data.name)


async def _create_author_async(*, data: _CreateAuthorInput) -> Author:
    return await Author.objects.acreate(name=data.name)


class _CreateAuthorView(ServiceCreateView):
    service = _create_author
    input_serializer = _CreateAuthorInput
    output_serializer = AuthorSerializer


class _AsyncCreateAuthorView(ServiceCreateView):
    service = _create_author_async
    input_serializer = _CreateAuthorInput
    output_serializer = AuthorSerializer


class _NoInputCreateView(ServiceCreateView):
    service = staticmethod(lambda: {"triggered": True})


factory = APIRequestFactory()


@pytest.mark.django_db
class TestServiceCreateView:
    def test_creates_and_returns_201(self) -> None:
        request = factory.post("/", {"name": "Ada"}, format="json")
        response = _CreateAuthorView.as_view()(request)
        assert response.status_code == 201
        assert response.data == {"id": Author.objects.get().id, "name": "Ada"}

    def test_validation_error_returns_400(self) -> None:
        request = factory.post("/", {}, format="json")
        response = _CreateAuthorView.as_view()(request)
        assert response.status_code == 400

    def test_async_service_dispatched(self) -> None:
        request = factory.post("/", {"name": "Alan"}, format="json")
        response = _AsyncCreateAuthorView.as_view()(request)
        assert response.status_code == 201
        assert Author.objects.filter(name="Alan").exists()

    def test_no_input_serializer_accepts_empty_body(self) -> None:
        request = factory.post("/", {}, format="json")
        response = _NoInputCreateView.as_view()(request)
        assert response.status_code == 201
        assert response.data == {"triggered": True}

    def test_service_validation_error_maps_to_400(self) -> None:
        def raises(*, data: _CreateAuthorInput) -> Any:
            raise ServiceValidationError({"name": ["taken"]})

        class _View(ServiceCreateView):
            service = raises
            input_serializer = _CreateAuthorInput
            atomic = False

        request = factory.post("/", {"name": "x"}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 400
        assert response.data == {"name": ["taken"]}

    def test_service_error_maps_to_422(self) -> None:
        def raises(*, data: _CreateAuthorInput) -> Any:
            raise ServiceError("nope")

        class _View(ServiceCreateView):
            service = raises
            input_serializer = _CreateAuthorInput
            atomic = False

        request = factory.post("/", {"name": "x"}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 422

    def test_missing_service_raises_not_implemented(self) -> None:
        class _Empty(ServiceCreateView):
            input_serializer = _CreateAuthorInput

        request = factory.post("/", {"name": "x"}, format="json")
        with pytest.raises(NotImplementedError):
            _Empty.as_view()(request)

    def test_get_service_kwargs_extras_passed(self) -> None:
        captured: dict[str, Any] = {}

        def fn(*, tenant: str) -> dict[str, Any]:
            captured["tenant"] = tenant
            return {"ok": True}

        class _View(ServiceCreateView):
            service = fn
            atomic = False

            def get_service_kwargs(self) -> dict[str, Any]:
                return {"tenant": "acme"}

        request = factory.post("/", {}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 201
        assert captured["tenant"] == "acme"

    def test_service_returning_none_renders_204(self) -> None:
        class _View(ServiceCreateView):
            service = staticmethod(lambda: None)
            atomic = False

        request = factory.post("/", {}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 204

    def test_output_selector_invoked(self) -> None:
        def fn() -> dict[str, Any]:
            return {"raw": True}

        def selector(*, result: dict[str, Any]) -> dict[str, Any]:
            return {**result, "rendered": True}

        class _View(ServiceCreateView):
            service = fn
            output_selector = selector
            atomic = False

        request = factory.post("/", {}, format="json")
        response = _View.as_view()(request)
        assert response.data == {"raw": True, "rendered": True}
