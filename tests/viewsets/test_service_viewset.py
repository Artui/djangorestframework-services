"""Tests for ServiceViewSet."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework.test import APIRequestFactory

from rest_framework_services import ServiceSpec, ServiceViewSet
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


@dataclass
class _AuthorIn:
    name: str


def _list_authors() -> Any:
    return Author.objects.all().order_by("id")


def _get_author(*, pk: int) -> Author | None:
    return Author.objects.filter(pk=pk).first()


def _create_author(*, data: _AuthorIn) -> Author:
    return Author.objects.create(name=data.name)


def _update_author(*, instance: Author, data: _AuthorIn) -> Author:
    instance.name = data.name
    instance.save(update_fields=["name"])
    return instance


def _delete_author(*, instance: Author) -> None:
    instance.delete()


class _AuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    serializer_classes = {
        "list": AuthorSerializer,
        "retrieve": AuthorSerializer,
    }
    service_specs = {
        "list": _list_authors,
        "retrieve": _get_author,
        "create": ServiceSpec(
            service=_create_author,
            input_serializer=_AuthorIn,
            output_serializer=AuthorSerializer,
        ),
        "update": ServiceSpec(
            service=_update_author,
            input_serializer=_AuthorIn,
            output_serializer=AuthorSerializer,
        ),
        "destroy": ServiceSpec(service=_delete_author),
    }


factory = APIRequestFactory()


@pytest.mark.django_db
class TestServiceViewSetActions:
    def test_list(self) -> None:
        Author.objects.create(name="a")
        Author.objects.create(name="b")
        request = factory.get("/")
        view = _AuthorViewSet.as_view({"get": "list"})
        response = view(request)
        assert response.status_code == 200
        assert [item["name"] for item in response.data] == ["a", "b"]

    def test_retrieve(self) -> None:
        author = Author.objects.create(name="Ada")
        request = factory.get("/")
        view = _AuthorViewSet.as_view({"get": "retrieve"})
        response = view(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data["name"] == "Ada"

    def test_create(self) -> None:
        request = factory.post("/", {"name": "Alan"}, format="json")
        view = _AuthorViewSet.as_view({"post": "create"})
        response = view(request)
        assert response.status_code == 201
        assert Author.objects.filter(name="Alan").exists()

    def test_update_put(self) -> None:
        author = Author.objects.create(name="orig")
        request = factory.put("/", {"name": "new"}, format="json")
        view = _AuthorViewSet.as_view({"put": "update"})
        response = view(request, pk=author.pk)
        assert response.status_code == 200
        author.refresh_from_db()
        assert author.name == "new"

    def test_partial_update_patch(self) -> None:
        author = Author.objects.create(name="orig")
        request = factory.patch("/", {"name": "patched"}, format="json")
        view = _AuthorViewSet.as_view({"patch": "partial_update"})
        response = view(request, pk=author.pk)
        assert response.status_code == 200
        author.refresh_from_db()
        assert author.name == "patched"

    def test_destroy(self) -> None:
        author = Author.objects.create(name="x")
        request = factory.delete("/")
        view = _AuthorViewSet.as_view({"delete": "destroy"})
        response = view(request, pk=author.pk)
        assert response.status_code == 204
        assert not Author.objects.exists()

    def test_kwargs_hooks_pass_extras(self) -> None:
        captured: dict[str, Any] = {}

        def listed(*, tenant: str) -> Any:
            captured["selector_tenant"] = tenant
            return []

        def created(*, tenant: str, data: _AuthorIn) -> dict[str, Any]:
            captured["service_tenant"] = tenant
            return {"name": data.name}

        class _View(ServiceViewSet):
            serializer_classes = {"list": AuthorSerializer}
            service_specs = {
                "list": listed,
                "create": ServiceSpec(service=created, input_serializer=_AuthorIn),
            }

            def get_selector_kwargs(self) -> dict[str, Any]:
                return {"tenant": "S"}

            def get_service_kwargs(self) -> dict[str, Any]:
                return {"tenant": "C"}

        view_list = _View.as_view({"get": "list"})
        view_list(factory.get("/"))
        assert captured["selector_tenant"] == "S"

        view_create = _View.as_view({"post": "create"})
        view_create(factory.post("/", {"name": "x"}, format="json"))
        assert captured["service_tenant"] == "C"


@pytest.mark.django_db
class TestServiceViewSetFallbacks:
    def test_list_falls_back_to_get_queryset_when_selector_missing(self) -> None:
        Author.objects.create(name="alpha")

        class _View(ServiceViewSet):
            queryset = Author.objects.all().order_by("id")
            serializer_classes = {"list": AuthorSerializer}

        view = _View.as_view({"get": "list"})
        response = view(factory.get("/"))
        assert response.status_code == 200
        assert response.data[0]["name"] == "alpha"

    def test_retrieve_falls_back_to_get_object_when_selector_missing(self) -> None:
        author = Author.objects.create(name="Ada")

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            serializer_classes = {"retrieve": AuthorSerializer}

        view = _View.as_view({"get": "retrieve"})
        response = view(factory.get("/"), pk=author.pk)
        assert response.status_code == 200
        assert response.data["name"] == "Ada"


@pytest.mark.django_db
class TestServiceViewSetEdgeCases:
    def test_retrieve_404_when_selector_returns_none(self) -> None:
        view = _AuthorViewSet.as_view({"get": "retrieve"})
        response = view(factory.get("/"), pk=999)
        assert response.status_code == 404

    def test_retrieve_404_when_does_not_exist_raised(self) -> None:
        def strict(*, pk: int) -> Author:
            return Author.objects.get(pk=pk)

        class _View(ServiceViewSet):
            serializer_classes = {"retrieve": AuthorSerializer}
            service_specs = {"retrieve": strict}

        view = _View.as_view({"get": "retrieve"})
        response = view(factory.get("/"), pk=999)
        assert response.status_code == 404

    def test_create_service_error_maps_to_422(self) -> None:
        from rest_framework_services import ServiceError

        def boom(*, data: _AuthorIn) -> None:
            raise ServiceError("nope")

        class _View(ServiceViewSet):
            service_specs = {
                "create": ServiceSpec(service=boom, input_serializer=_AuthorIn, atomic=False),
            }

        view = _View.as_view({"post": "create"})
        response = view(factory.post("/", {"name": "x"}, format="json"))
        assert response.status_code == 422

    def test_create_with_output_selector(self) -> None:
        def fn(*, data: _AuthorIn) -> str:
            return data.name

        def selector(*, result: str) -> str:
            return result.upper()

        class _View(ServiceViewSet):
            service_specs = {
                "create": ServiceSpec(
                    service=fn,
                    input_serializer=_AuthorIn,
                    output_selector=selector,
                    atomic=False,
                ),
            }

        view = _View.as_view({"post": "create"})
        response = view(factory.post("/", {"name": "ada"}, format="json"))
        assert response.status_code == 201
        assert response.data == "ADA"

    def test_create_returning_none_renders_204(self) -> None:
        class _View(ServiceViewSet):
            service_specs = {
                "create": ServiceSpec(
                    service=staticmethod(lambda *, data: None),
                    input_serializer=_AuthorIn,
                    atomic=False,
                ),
            }

        view = _View.as_view({"post": "create"})
        response = view(factory.post("/", {"name": "x"}, format="json"))
        assert response.status_code == 204

    def test_destroy_with_output_serializer(self) -> None:
        def fn(*, instance: Author) -> Author:
            return instance

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            service_specs = {
                "destroy": ServiceSpec(
                    service=fn, output_serializer=AuthorSerializer, atomic=False
                ),
            }

        author = Author.objects.create(name="Z")
        view = _View.as_view({"delete": "destroy"})
        response = view(factory.delete("/"), pk=author.pk)
        assert response.status_code == 204
        # 204 because that's destroy's success_status; output_serializer is
        # still called but Response uses the configured status.

    def test_get_serializer_class_falls_back_for_other_actions(self) -> None:
        # When ``self.action`` isn't in ``serializer_classes`` (e.g. for
        # ``create``), DRF's ``super().get_serializer_class()`` runs.
        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            serializer_class = AuthorSerializer
            serializer_classes = {"list": AuthorSerializer}

        instance = _View()
        instance.action = "create"
        assert instance.get_serializer_class() is AuthorSerializer


@pytest.mark.django_db
class TestServiceViewSetUnconfigured:
    def test_create_without_service_returns_405(self) -> None:
        class _View(ServiceViewSet):
            pass

        view = _View.as_view({"post": "create"})
        response = view(factory.post("/"))
        assert response.status_code == 405

    def test_update_without_service_returns_405(self) -> None:
        class _View(ServiceViewSet):
            queryset = Author.objects.all()

        view = _View.as_view({"put": "update"})
        author = Author.objects.create(name="x")
        response = view(factory.put("/"), pk=author.pk)
        assert response.status_code == 405

    def test_partial_update_without_service_returns_405(self) -> None:
        class _View(ServiceViewSet):
            queryset = Author.objects.all()

        view = _View.as_view({"patch": "partial_update"})
        author = Author.objects.create(name="x")
        response = view(factory.patch("/"), pk=author.pk)
        assert response.status_code == 405

    def test_destroy_without_service_returns_405(self) -> None:
        class _View(ServiceViewSet):
            queryset = Author.objects.all()

        view = _View.as_view({"delete": "destroy"})
        author = Author.objects.create(name="x")
        response = view(factory.delete("/"), pk=author.pk)
        assert response.status_code == 405

    def test_create_with_non_servicespec_entry_returns_405(self) -> None:
        # A bare callable (selector-style) under "create" is a misconfiguration —
        # the write mixin treats it as "not configured" rather than crashing.
        class _View(ServiceViewSet):
            service_specs = {"create": lambda *, data: None}

        view = _View.as_view({"post": "create"})
        response = view(factory.post("/"))
        assert response.status_code == 405


@pytest.mark.django_db
class TestServiceViewSetSpecOverrides:
    def test_create_spec_success_status_override(self) -> None:
        class _View(ServiceViewSet):
            service_specs = {
                "create": ServiceSpec(
                    service=staticmethod(lambda *, data: {"x": data.name}),
                    input_serializer=_AuthorIn,
                    atomic=False,
                    success_status=202,
                ),
            }

        view = _View.as_view({"post": "create"})
        response = view(factory.post("/", {"name": "x"}, format="json"))
        assert response.status_code == 202

    def test_update_spec_success_status_override(self) -> None:
        author = Author.objects.create(name="x")

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            service_specs = {
                "update": ServiceSpec(
                    service=_update_author,
                    input_serializer=_AuthorIn,
                    atomic=False,
                    success_status=202,
                ),
            }

        view = _View.as_view({"put": "update"})
        response = view(factory.put("/", {"name": "y"}, format="json"), pk=author.pk)
        assert response.status_code == 202

    def test_destroy_spec_success_status_override(self) -> None:
        author = Author.objects.create(name="x")

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            service_specs = {
                "destroy": ServiceSpec(service=_delete_author, atomic=False, success_status=200),
            }

        view = _View.as_view({"delete": "destroy"})
        response = view(factory.delete("/"), pk=author.pk)
        assert response.status_code == 200
