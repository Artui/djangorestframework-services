"""Tests for ServiceCreateView."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceCreateView,
    ServiceError,
    ServiceSpec,
    ServiceValidationError,
)
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
    spec = ServiceSpec(
        service=_create_author,
        input_serializer=_CreateAuthorInput,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
        ),
    )


class _AsyncCreateAuthorView(ServiceCreateView):
    spec = ServiceSpec(
        service=_create_author_async,
        input_serializer=_CreateAuthorInput,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
        ),
    )


class _NoInputCreateView(ServiceCreateView):
    spec = ServiceSpec(service=staticmethod(lambda: {"triggered": True}))


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

    def test_spec_kwargs_provider_passes_extras(self) -> None:
        captured: dict[str, Any] = {}

        def fn(*, data: _CreateAuthorInput, tenant_id: int) -> Author:
            captured["tenant_id"] = tenant_id
            return Author.objects.create(name=data.name)

        def provider(view: Any, request: Any) -> dict[str, Any]:
            return {"tenant_id": 99}

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=fn,
                input_serializer=_CreateAuthorInput,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
                kwargs=provider,
            )

        request = factory.post("/", {"name": "Tina"}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 201
        assert captured["tenant_id"] == 99

    def test_service_does_not_receive_view_in_pool(self) -> None:
        """Regression: ``view`` was previously injected; it must not be anymore."""
        captured: dict[str, Any] = {}

        def fn(*, data: _CreateAuthorInput, **kwargs: Any) -> Author:
            captured.update(kwargs)
            return Author.objects.create(name=data.name)

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=fn,
                input_serializer=_CreateAuthorInput,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
            )

        request = factory.post("/", {"name": "Bob"}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 201
        assert "view" not in captured
        assert "request" in captured
        assert "user" in captured

    def test_service_validation_error_maps_to_400(self) -> None:
        def raises(*, data: _CreateAuthorInput) -> Any:
            raise ServiceValidationError({"name": ["taken"]})

        class _View(ServiceCreateView):
            spec = ServiceSpec(service=raises, input_serializer=_CreateAuthorInput, atomic=False)

        request = factory.post("/", {"name": "x"}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 400
        assert response.data == {"name": ["taken"]}

    def test_service_error_maps_to_422(self) -> None:
        def raises(*, data: _CreateAuthorInput) -> Any:
            raise ServiceError("nope")

        class _View(ServiceCreateView):
            spec = ServiceSpec(service=raises, input_serializer=_CreateAuthorInput, atomic=False)

        request = factory.post("/", {"name": "x"}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 422

    def test_missing_spec_raises_not_implemented(self) -> None:
        class _Empty(ServiceCreateView): ...

        request = factory.post("/", {"name": "x"}, format="json")
        with pytest.raises(NotImplementedError):
            _Empty.as_view()(request)

    def test_get_service_kwargs_extras_passed(self) -> None:
        captured: dict[str, Any] = {}

        def fn(*, tenant: str) -> dict[str, Any]:
            captured["tenant"] = tenant
            return {"ok": True}

        class _View(ServiceCreateView):
            spec = ServiceSpec(service=fn, atomic=False)

            def get_service_kwargs(self) -> dict[str, Any]:
                return {"tenant": "acme"}

        request = factory.post("/", {}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 201
        assert captured["tenant"] == "acme"

    def test_service_returning_none_renders_204(self) -> None:
        class _View(ServiceCreateView):
            spec = ServiceSpec(service=staticmethod(lambda: None), atomic=False)

        request = factory.post("/", {}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 204

    def test_output_selector_invoked(self) -> None:
        def fn() -> dict[str, Any]:
            return {"raw": True}

        def selector(*, result: dict[str, Any]) -> dict[str, Any]:
            return {**result, "rendered": True}

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=fn,
                output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector),
                atomic=False,
            )

        request = factory.post("/", {}, format="json")
        response = _View.as_view()(request)
        assert response.data == {"raw": True, "rendered": True}

    def test_spec_success_status_override(self) -> None:
        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=staticmethod(lambda: {"ok": True}),
                atomic=False,
                success_status=202,
            )

        request = factory.post("/", {}, format="json")
        response = _View.as_view()(request)
        assert response.status_code == 202

    def test_input_data_lifts_path_kwargs_into_serializer(self) -> None:
        @dataclass
        class _NestedIn:
            name: str
            parent_id: int

        captured: dict[str, Any] = {}

        def fn(*, data: _NestedIn) -> dict[str, Any]:
            captured["data"] = data
            return {"name": data.name}

        def spec_input(view: Any, request: Any) -> dict[str, Any]:
            return {"parent_id": int(view.kwargs["parent_id"])}

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=fn,
                input_serializer=_NestedIn,
                input_data=spec_input,
                atomic=False,
            )

        request = factory.post("/", {"name": "Ada"}, format="json")
        response = _View.as_view()(request, parent_id=7)
        assert response.status_code == 201
        assert captured["data"].name == "Ada"
        assert captured["data"].parent_id == 7

    def test_input_data_view_catch_all_hook(self) -> None:
        @dataclass
        class _NestedIn:
            name: str
            parent_id: int

        captured: dict[str, Any] = {}

        def fn(*, data: _NestedIn) -> dict[str, Any]:
            captured["data"] = data
            return {"name": data.name}

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=fn,
                input_serializer=_NestedIn,
                atomic=False,
            )

            def get_input_data(self, request: Any) -> dict[str, Any]:
                return {"parent_id": int(self.kwargs["parent_id"])}

        request = factory.post("/", {"name": "Ada"}, format="json")
        response = _View.as_view()(request, parent_id=11)
        assert response.status_code == 201
        assert captured["data"].parent_id == 11
