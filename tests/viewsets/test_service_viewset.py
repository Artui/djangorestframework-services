"""Tests for ServiceViewSet."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.test import APIRequestFactory

from rest_framework_services import SelectorSpec, ServiceSpec, ServiceViewSet
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
    action_specs = {
        "list": SelectorSpec(selector=_list_authors, output_serializer=AuthorSerializer),
        "retrieve": SelectorSpec(selector=_get_author, output_serializer=AuthorSerializer),
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

    def test_destroy_with_input_serializer(self) -> None:
        @dataclass
        class _Reason:
            reason: str

        captured: dict[str, Any] = {}

        def fn(*, instance: Author, data: _Reason) -> None:
            captured["reason"] = data.reason
            instance.delete()

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "destroy": ServiceSpec(service=fn, input_serializer=_Reason),
            }

        author = Author.objects.create(name="x")
        request = factory.delete("/", {"reason": "spam"}, format="json")
        view = _View.as_view({"delete": "destroy"})
        response = view(request, pk=author.pk)
        assert response.status_code == 204
        assert captured["reason"] == "spam"
        assert not Author.objects.filter(pk=author.pk).exists()

    def test_kwargs_hooks_pass_extras(self) -> None:
        captured: dict[str, Any] = {}

        def listed(*, tenant: str) -> Any:
            captured["selector_tenant"] = tenant
            return []

        def created(*, tenant: str, data: _AuthorIn) -> dict[str, Any]:
            captured["service_tenant"] = tenant
            return {"name": data.name}

        class _View(ServiceViewSet):
            action_specs = {
                "list": SelectorSpec(selector=listed, output_serializer=AuthorSerializer),
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

    def test_spec_kwargs_provider_passes_extras(self) -> None:
        captured: dict[str, Any] = {}

        def created(*, data: _AuthorIn, tenant_id: int) -> dict[str, Any]:
            captured["service_tenant_id"] = tenant_id
            return {"name": data.name}

        def listed(*, tenant_id: int) -> Any:
            captured["selector_tenant_id"] = tenant_id
            return []

        def create_kwargs(view: Any, request: Any) -> dict[str, Any]:
            return {"tenant_id": 42}

        def list_kwargs(view: Any, request: Any) -> dict[str, Any]:
            return {"tenant_id": 7}

        class _View(ServiceViewSet):
            action_specs = {
                "list": SelectorSpec(
                    selector=listed,
                    output_serializer=AuthorSerializer,
                    kwargs=list_kwargs,
                ),
                "create": ServiceSpec(
                    service=created,
                    input_serializer=_AuthorIn,
                    kwargs=create_kwargs,
                ),
            }

        _View.as_view({"get": "list"})(factory.get("/"))
        assert captured["selector_tenant_id"] == 7

        _View.as_view({"post": "create"})(factory.post("/", {"name": "x"}, format="json"))
        assert captured["service_tenant_id"] == 42

    def test_per_action_hook_overrides_catch_all(self) -> None:
        captured: dict[str, Any] = {}

        def created(*, data: _AuthorIn, tag: str) -> dict[str, Any]:
            captured["create_tag"] = tag
            return {"name": data.name}

        def updated(*, instance: Author, data: _AuthorIn, tag: str) -> Author:
            captured["update_tag"] = tag
            instance.name = data.name
            instance.save(update_fields=["name"])
            return instance

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "create": ServiceSpec(service=created, input_serializer=_AuthorIn),
                "update": ServiceSpec(service=updated, input_serializer=_AuthorIn),
            }

            def get_service_kwargs(self) -> dict[str, Any]:
                return {"tag": "default"}

            def get_create_service_kwargs(self) -> dict[str, Any]:
                return {"tag": "create-only"}

        _View.as_view({"post": "create"})(factory.post("/", {"name": "x"}, format="json"))
        assert captured["create_tag"] == "create-only"

        author = Author.objects.create(name="orig")
        _View.as_view({"put": "update"})(
            factory.put("/", {"name": "new"}, format="json"), pk=author.pk
        )
        assert captured["update_tag"] == "default"

    def test_spec_kwargs_overrides_per_action_hook_and_catch_all(self) -> None:
        captured: dict[str, Any] = {}

        def created(*, data: _AuthorIn, tag: str) -> dict[str, Any]:
            captured["tag"] = tag
            return {"name": data.name}

        def create_kwargs(view: Any, request: Any) -> dict[str, Any]:
            return {"tag": "spec"}

        class _View(ServiceViewSet):
            action_specs = {
                "create": ServiceSpec(
                    service=created,
                    input_serializer=_AuthorIn,
                    kwargs=create_kwargs,
                ),
            }

            def get_service_kwargs(self) -> dict[str, Any]:
                return {"tag": "catch-all"}

            def get_create_service_kwargs(self) -> dict[str, Any]:
                return {"tag": "action"}

        _View.as_view({"post": "create"})(factory.post("/", {"name": "x"}, format="json"))
        assert captured["tag"] == "spec"

    def test_input_data_three_tier_chain(self) -> None:
        @dataclass
        class _NestedIn:
            name: str
            parent_id: int
            tag: str

        captured: dict[str, Any] = {}

        def created(*, data: _NestedIn) -> dict[str, Any]:
            captured["data"] = data
            return {"name": data.name}

        def spec_input(view: Any, request: Any) -> dict[str, Any]:
            return {"parent_id": int(view.kwargs["parent_id"])}

        class _View(ServiceViewSet):
            action_specs = {
                "create": ServiceSpec(
                    service=created,
                    input_serializer=_NestedIn,
                    input_data=spec_input,
                ),
            }

            def get_input_data(self, request: Any) -> dict[str, Any]:
                return {"tag": "catch", "parent_id": -1}

            def get_create_input_data(self, request: Any) -> dict[str, Any]:
                return {"tag": "action", "parent_id": -2}

        response = _View.as_view({"post": "create"})(
            factory.post("/", {"name": "Ada"}, format="json"), parent_id=42
        )
        assert response.status_code == 201
        assert captured["data"].name == "Ada"
        # spec layer wins over per-action and catch-all on parent_id;
        # action layer wins over catch-all on tag.
        assert captured["data"].parent_id == 42
        assert captured["data"].tag == "action"

    def test_spec_kwargs_provider_receives_view_and_request(self) -> None:
        captured: dict[str, Any] = {}

        def created(*, data: _AuthorIn, action_seen: str) -> dict[str, Any]:
            return {"name": data.name, "action": action_seen}

        def create_kwargs(view: Any, request: Any) -> dict[str, Any]:
            captured["action"] = view.action
            captured["has_request"] = request is not None
            return {"action_seen": view.action or ""}

        class _View(ServiceViewSet):
            action_specs = {
                "create": ServiceSpec(
                    service=created,
                    input_serializer=_AuthorIn,
                    kwargs=create_kwargs,
                ),
            }

        response = _View.as_view({"post": "create"})(
            factory.post("/", {"name": "x"}, format="json")
        )
        assert response.status_code == 201
        assert captured["action"] == "create"
        assert captured["has_request"] is True

    def test_per_action_selector_kwargs_hook(self) -> None:
        captured: dict[str, Any] = {}

        def listed(*, marker: str) -> Any:
            captured["marker"] = marker
            return []

        class _View(ServiceViewSet):
            action_specs = {
                "list": SelectorSpec(selector=listed, output_serializer=AuthorSerializer),
            }

            def get_selector_kwargs(self) -> dict[str, Any]:
                return {"marker": "default"}

            def get_list_selector_kwargs(self) -> dict[str, Any]:
                return {"marker": "list-only"}

        _View.as_view({"get": "list"})(factory.get("/"))
        assert captured["marker"] == "list-only"


@pytest.mark.django_db
class TestServiceViewSetFallbacks:
    def test_list_falls_back_to_get_queryset_when_selector_missing(self) -> None:
        Author.objects.create(name="alpha")

        class _View(ServiceViewSet):
            queryset = Author.objects.all().order_by("id")
            action_specs = {
                "list": SelectorSpec(output_serializer=AuthorSerializer),
            }

        view = _View.as_view({"get": "list"})
        response = view(factory.get("/"))
        assert response.status_code == 200
        assert response.data[0]["name"] == "alpha"

    def test_retrieve_falls_back_to_get_object_when_selector_missing(self) -> None:
        author = Author.objects.create(name="Ada")

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "retrieve": SelectorSpec(output_serializer=AuthorSerializer),
            }

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
            action_specs = {
                "retrieve": SelectorSpec(selector=strict, output_serializer=AuthorSerializer),
            }

        view = _View.as_view({"get": "retrieve"})
        response = view(factory.get("/"), pk=999)
        assert response.status_code == 404

    def test_create_service_error_maps_to_422(self) -> None:
        from rest_framework_services import ServiceError

        def boom(*, data: _AuthorIn) -> None:
            raise ServiceError("nope")

        class _View(ServiceViewSet):
            action_specs = {
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
            action_specs = {
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
            action_specs = {
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
            action_specs = {
                "destroy": ServiceSpec(
                    service=fn, output_serializer=AuthorSerializer, atomic=False
                ),
            }

        author = Author.objects.create(name="Z")
        view = _View.as_view({"delete": "destroy"})
        response = view(factory.delete("/"), pk=author.pk)
        assert response.status_code == 204

    def test_get_serializer_class_falls_back_for_other_actions(self) -> None:
        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            serializer_class = AuthorSerializer
            action_specs = {
                "list": SelectorSpec(output_serializer=AuthorSerializer),
            }

        instance = _View()
        instance.action = "create"
        assert instance.get_serializer_class() is AuthorSerializer


@pytest.mark.django_db
class TestServiceViewSetUnconfigured:
    def test_create_without_service_returns_405(self) -> None:
        class _View(ServiceViewSet): ...

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

    def test_create_with_selector_spec_raises_improperly_configured(self) -> None:
        class _View(ServiceViewSet):
            action_specs = {
                "create": SelectorSpec(selector=lambda: None),
            }

        view = _View.as_view({"post": "create"})
        with pytest.raises(ImproperlyConfigured):
            view(factory.post("/"))

    def test_update_with_selector_spec_raises_improperly_configured(self) -> None:
        author = Author.objects.create(name="x")

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "update": SelectorSpec(selector=lambda: None),
            }

        view = _View.as_view({"put": "update"})
        with pytest.raises(ImproperlyConfigured):
            view(factory.put("/", {"name": "y"}, format="json"), pk=author.pk)

    def test_destroy_with_selector_spec_raises_improperly_configured(self) -> None:
        author = Author.objects.create(name="x")

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "destroy": SelectorSpec(selector=lambda: None),
            }

        view = _View.as_view({"delete": "destroy"})
        with pytest.raises(ImproperlyConfigured):
            view(factory.delete("/"), pk=author.pk)

    def test_list_with_service_spec_raises_improperly_configured(self) -> None:
        def _noop() -> None: ...

        class _View(ServiceViewSet):
            action_specs = {
                "list": ServiceSpec(service=_noop),
            }

        view = _View.as_view({"get": "list"})
        with pytest.raises(ImproperlyConfigured):
            view(factory.get("/"))

    def test_retrieve_with_service_spec_raises_improperly_configured(self) -> None:
        def _noop() -> None: ...

        class _View(ServiceViewSet):
            action_specs = {
                "retrieve": ServiceSpec(service=_noop),
            }

        view = _View.as_view({"get": "retrieve"})
        with pytest.raises(ImproperlyConfigured):
            view(factory.get("/"), pk=1)


@pytest.mark.django_db
class TestServiceViewSetSpecOverrides:
    def test_create_spec_success_status_override(self) -> None:
        class _View(ServiceViewSet):
            action_specs = {
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
            action_specs = {
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
            action_specs = {
                "destroy": ServiceSpec(service=_delete_author, atomic=False, success_status=200),
            }

        view = _View.as_view({"delete": "destroy"})
        response = view(factory.delete("/"), pk=author.pk)
        assert response.status_code == 200
