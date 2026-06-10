"""End-to-end schema generation with drf-spectacular + ``ServiceAutoSchema``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.urls import path
from drf_spectacular.generators import SchemaGenerator
from rest_framework.routers import DefaultRouter
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceCreateView,
    ServiceDeleteView,
    ServiceSpec,
    ServiceUpdateView,
    ServiceViewSet,
    service_action,
)
from rest_framework_services.openapi import enable_openapi
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


@dataclass
class _AuthorIn:
    name: str


@dataclass
class _AuthorOut:
    id: int
    name: str


def _create(*, data: _AuthorIn) -> _AuthorOut:
    return _AuthorOut(id=1, name=data.name)


def _update(*, instance: Any, data: _AuthorIn) -> _AuthorOut:
    return _AuthorOut(id=1, name=data.name)


def _approve(*, instance: Any) -> dict[str, Any]:
    return {"approved": True}


def _list_authors() -> Any:
    return Author.objects.all().order_by("id")


class _CreateView(ServiceCreateView):
    spec = ServiceSpec(
        service=_create,
        input_serializer=_AuthorIn,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
        ),
    )


class _UpdateView(ServiceUpdateView):
    queryset = Author.objects.all()
    spec = ServiceSpec(
        service=_update,
        input_serializer=_AuthorIn,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
        ),
    )


@dataclass
class _DeleteReason:
    reason: str


def _delete_with_reason(*, instance: Any, data: _DeleteReason) -> None: ...


class _DeleteView(ServiceDeleteView):
    queryset = Author.objects.all()
    spec = ServiceSpec(service=_delete_with_reason, input_serializer=_DeleteReason)


def _delete_plain(*, instance: Any) -> None: ...


class _DeletePlainView(ServiceDeleteView):
    queryset = Author.objects.all()
    spec = ServiceSpec(service=_delete_plain)


class _AuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {
        "create": ServiceSpec(
            service=_create,
            input_serializer=_AuthorIn,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
            ),
        ),
        "list": SelectorSpec(
            kind=SelectorKind.LIST, selector=_list_authors, output_serializer=AuthorSerializer
        ),
    }


class _ApproveViewSet(GenericViewSet):
    queryset = Author.objects.all()

    @service_action(
        ServiceSpec(
            service=_approve,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
            ),
        ),
        detail=True,
        methods=["post"],
    )
    def approve(self, request, pk=None):  # type: ignore[no-untyped-def]
        ...


class _ForcedFullUpdateView(ServiceUpdateView):
    queryset = Author.objects.all()
    spec = ServiceSpec(
        service=_update,
        input_serializer=_AuthorIn,
        partial=False,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
        ),
    )


_router = DefaultRouter()
_router.register("authors", _AuthorViewSet, basename="authors")
_router.register("approvals", _ApproveViewSet, basename="approvals")

urlpatterns = [
    path("create/", _CreateView.as_view()),
    path("update/<int:pk>/", _UpdateView.as_view()),
    path("forced-full/<int:pk>/", _ForcedFullUpdateView.as_view()),
    path("delete/<int:pk>/", _DeleteView.as_view()),
    path("plain-delete/<int:pk>/", _DeletePlainView.as_view()),
    *_router.urls,
]


@pytest.fixture(scope="module", autouse=True)
def _enable_openapi() -> None:
    enable_openapi()


def _generate() -> dict[str, Any]:
    return SchemaGenerator(patterns=urlpatterns).get_schema(request=None, public=True)


@pytest.mark.django_db
class TestStandaloneViewSchema:
    def test_create_view_emits_201_response(self) -> None:
        schema = _generate()
        op = schema["paths"]["/create/"]["post"]
        assert "201" in op["responses"]
        assert "422" in op["responses"]

    def test_create_view_request_body_uses_input_serializer(self) -> None:
        schema = _generate()
        op = schema["paths"]["/create/"]["post"]
        body = op["requestBody"]["content"]["application/json"]["schema"]
        # The schema is a $ref to a component derived from ``_AuthorIn``;
        # follow it to verify the component has typed properties (not a bare
        # ``object``).
        ref = body["$ref"]
        assert ref.startswith("#/components/schemas/")
        component = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
        assert set(component.get("properties", {}).keys()) == {"name"}

    def test_partial_update_request_serializer_marked_partial(self) -> None:
        schema = _generate()
        op = schema["paths"]["/update/{id}/"]["patch"]
        body = op["requestBody"]["content"]["application/json"]["schema"]
        ref = body["$ref"]
        component = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
        # spectacular emits a ``Patched*`` component for partial requests.
        assert "Patched" in ref or component.get("required") in (None, [])

    def test_spec_partial_false_overrides_patch_partiality(self) -> None:
        schema = _generate()
        op = schema["paths"]["/forced-full/{id}/"]["patch"]
        body = op["requestBody"]["content"]["application/json"]["schema"]
        ref = body["$ref"]
        component = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
        # ``partial=False`` forces full-update semantics, so no ``Patched*``
        # component and the required list survives under PATCH.
        assert "Patched" not in ref
        assert component.get("required") == ["name"]


@pytest.mark.django_db
class TestDeleteViewSchema:
    def test_delete_view_emits_request_body_when_input_serializer_set(self) -> None:
        schema = _generate()
        op = schema["paths"]["/delete/{id}/"]["delete"]
        assert "requestBody" in op
        body = op["requestBody"]["content"]["application/json"]["schema"]
        ref = body["$ref"]
        component = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
        assert set(component.get("properties", {}).keys()) == {"reason"}

    def test_delete_view_omits_request_body_when_no_input_serializer(self) -> None:
        schema = _generate()
        op = schema["paths"]["/plain-delete/{id}/"]["delete"]
        assert "requestBody" not in op


@pytest.mark.django_db
class TestViewsetSchema:
    def test_create_action_schema_uses_service_spec(self) -> None:
        schema = _generate()
        op = schema["paths"]["/authors/"]["post"]
        assert "201" in op["responses"]
        assert "422" in op["responses"]
        body = op["requestBody"]["content"]["application/json"]["schema"]
        assert body["$ref"].startswith("#/components/schemas/")

    def test_list_action_schema_left_alone(self) -> None:
        schema = _generate()
        op = schema["paths"]["/authors/"]["get"]
        # Selector list reads ``output_serializer`` via ``ActionSerializerResolver``;
        # the AutoSchema does not attach a 422 to it.
        assert "200" in op["responses"]
        assert "422" not in op["responses"]


@pytest.mark.django_db
class TestServiceActionSchema:
    def test_action_emits_request_and_response(self) -> None:
        schema = _generate()
        # ``approve`` is a detail action; spectacular places it under
        # /approvals/{id}/approve/.
        op = schema["paths"]["/approvals/{id}/approve/"]["post"]
        assert "200" in op["responses"]
        assert "422" in op["responses"]


class TestServiceErrorSerializer:
    def test_422_response_references_service_error(self) -> None:
        schema = _generate()
        op = schema["paths"]["/create/"]["post"]
        ref = op["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
        component_name = ref.rsplit("/", 1)[1]
        component = schema["components"]["schemas"][component_name]
        assert "detail" in component["properties"]
