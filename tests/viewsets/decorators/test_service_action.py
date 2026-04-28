"""Tests for the @service_action decorator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import ServiceSpec, service_action
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


@dataclass
class _ApproveInput:
    note: str = ""


def _approve(*, instance: Author, data: _ApproveInput) -> dict[str, Any]:
    return {"id": instance.pk, "note": data.note}


def _bulk_announce(*, data: _ApproveInput) -> dict[str, Any]:
    return {"announced": data.note}


factory = APIRequestFactory()


class _ApproveViewSet(GenericViewSet):
    queryset = Author.objects.all()

    @service_action(
        ServiceSpec(service=_approve, input_serializer=_ApproveInput),
        detail=True,
        methods=["post"],
    )
    def approve(self, request, pk=None):  # type: ignore[no-untyped-def]
        """Stubbed body — replaced by service_action."""

    @service_action(
        ServiceSpec(
            service=_bulk_announce,
            input_serializer=_ApproveInput,
            success_status=202,
        ),
        detail=False,
        methods=["post"],
        url_path="announce",
        url_name="bulk-announce",
    )
    def announce(self, request):  # type: ignore[no-untyped-def]
        """Stubbed."""


@pytest.mark.django_db
class TestServiceAction:
    def test_detail_action_runs_with_instance(self) -> None:
        author = Author.objects.create(name="x")
        request = factory.post("/", {"note": "yes"}, format="json")
        view = _ApproveViewSet.as_view({"post": "approve"})
        response = view(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data == {"id": author.pk, "note": "yes"}

    def test_non_detail_action_with_custom_status_and_url(self) -> None:
        request = factory.post("/", {"note": "go"}, format="json")
        view = _ApproveViewSet.as_view({"post": "announce"})
        response = view(request)
        assert response.status_code == 202
        assert response.data == {"announced": "go"}

    def test_action_metadata_attached(self) -> None:
        announce = _ApproveViewSet.announce  # type: ignore[attr-defined]
        # DRF's @action decorator stamps these onto the function.
        assert announce.detail is False
        assert announce.url_path == "announce"
        assert announce.url_name == "bulk-announce"

    def test_uses_get_service_kwargs_when_present(self) -> None:
        captured: dict[str, Any] = {}

        def fn(*, tenant: str, data: _ApproveInput) -> dict[str, Any]:
            captured["tenant"] = tenant
            return {"ok": True}

        class _View(GenericViewSet):
            def get_service_kwargs(self) -> dict[str, Any]:
                return {"tenant": "T"}

            @service_action(
                ServiceSpec(service=fn, input_serializer=_ApproveInput),
                detail=False,
                methods=["post"],
            )
            def go(self, request):  # type: ignore[no-untyped-def]
                pass

        request = factory.post("/", {}, format="json")
        view = _View.as_view({"post": "go"})
        view(request)
        assert captured["tenant"] == "T"

    def test_spec_kwargs_provider_passes_extras(self) -> None:
        captured: dict[str, Any] = {}

        def fn(*, data: _ApproveInput, tenant_id: int) -> dict[str, Any]:
            captured["tenant_id"] = tenant_id
            return {"ok": True}

        def provider(view: Any, request: Any) -> dict[str, Any]:
            return {"tenant_id": 33}

        class _View(GenericViewSet):
            @service_action(
                ServiceSpec(service=fn, input_serializer=_ApproveInput, kwargs=provider),
                detail=False,
                methods=["post"],
            )
            def go(self, request):  # type: ignore[no-untyped-def]
                pass

        request = factory.post("/", {}, format="json")
        view = _View.as_view({"post": "go"})
        view(request)
        assert captured["tenant_id"] == 33

    def test_per_action_hook_overrides_catch_all(self) -> None:
        captured: dict[str, Any] = {}

        def fn(*, data: _ApproveInput, tag: str) -> dict[str, Any]:
            captured["tag"] = tag
            return {"ok": True}

        class _View(GenericViewSet):
            def get_service_kwargs(self) -> dict[str, Any]:
                return {"tag": "default"}

            def get_go_service_kwargs(self) -> dict[str, Any]:
                return {"tag": "action"}

            @service_action(
                ServiceSpec(service=fn, input_serializer=_ApproveInput),
                detail=False,
                methods=["post"],
            )
            def go(self, request):  # type: ignore[no-untyped-def]
                pass

        request = factory.post("/", {}, format="json")
        view = _View.as_view({"post": "go"})
        view(request)
        assert captured["tag"] == "action"

    def test_works_without_get_service_kwargs(self) -> None:
        # GenericViewSet does not define get_service_kwargs; ensure the
        # decorator still works.
        request = factory.post("/", {"note": "hi"}, format="json")
        view = _ApproveViewSet.as_view({"post": "announce"})
        response = view(request)
        assert response.status_code == 202

    def test_service_error_maps_to_422(self) -> None:
        from rest_framework_services import ServiceError

        def boom(*, data: _ApproveInput) -> None:
            raise ServiceError("nope")

        class _View(GenericViewSet):
            @service_action(
                ServiceSpec(service=boom, input_serializer=_ApproveInput, atomic=False),
                detail=False,
                methods=["post"],
            )
            def go(self, request):  # type: ignore[no-untyped-def]
                pass

        view = _View.as_view({"post": "go"})
        response = view(factory.post("/", {}, format="json"))
        assert response.status_code == 422

    def test_output_selector_invoked(self) -> None:
        def fn() -> str:
            return "raw"

        def selector(*, result: str) -> str:
            return result.upper()

        class _View(GenericViewSet):
            @service_action(
                ServiceSpec(service=fn, output_selector=selector, atomic=False),
                detail=False,
                methods=["post"],
            )
            def go(self, request):  # type: ignore[no-untyped-def]
                pass

        view = _View.as_view({"post": "go"})
        response = view(factory.post("/"))
        assert response.data == "RAW"

    def test_output_serializer_renders_body(self) -> None:
        def fn() -> dict[str, Any]:
            return {"name": "Ada"}

        class _Serializer(AuthorSerializer):
            class Meta(AuthorSerializer.Meta):
                fields = ("name",)

        class _View(GenericViewSet):
            @service_action(
                ServiceSpec(service=fn, output_serializer=_Serializer, atomic=False),
                detail=False,
                methods=["post"],
            )
            def go(self, request):  # type: ignore[no-untyped-def]
                pass

        view = _View.as_view({"post": "go"})
        response = view(factory.post("/"))
        assert response.status_code == 200
        assert response.data == {"name": "Ada"}

    def test_returning_none_with_no_instance_renders_204(self) -> None:
        def fn() -> None:
            return None

        class _View(GenericViewSet):
            @service_action(
                ServiceSpec(service=fn, atomic=False),
                detail=False,
                methods=["post"],
            )
            def go(self, request):  # type: ignore[no-untyped-def]
                pass

        view = _View.as_view({"post": "go"})
        response = view(factory.post("/"))
        assert response.status_code == 204

    def test_returning_none_with_instance_falls_back_to_instance(self) -> None:
        from tests.testapp.serializers import AuthorSerializer as Ser

        def fn(*, instance: Author) -> None:
            return None

        class _View(GenericViewSet):
            queryset = Author.objects.all()

            @service_action(
                ServiceSpec(service=fn, output_serializer=Ser, atomic=False),
                detail=True,
                methods=["post"],
            )
            def go(self, request, pk=None):  # type: ignore[no-untyped-def]
                pass

        author = Author.objects.create(name="A")
        view = _View.as_view({"post": "go"})
        response = view(factory.post("/"), pk=author.pk)
        assert response.status_code == 200
        assert response.data["name"] == "A"

    def test_methods_default_to_drf_action_default(self) -> None:
        # Calling service_action without explicit `methods` should leave
        # the choice to DRF's `@action` decorator (which defaults to GET).
        def fn() -> dict[str, Any]:
            return {"ok": True}

        class _View(GenericViewSet):
            @service_action(ServiceSpec(service=fn), detail=False)
            def ping(self, request):  # type: ignore[no-untyped-def]
                pass

        # DRF's @action stamps mapping with default methods.
        assert "get" in _View.ping.mapping  # type: ignore[attr-defined]
        view = _View.as_view({"get": "ping"})
        response = view(factory.get("/"))
        assert response.status_code == 200
        assert response.data == {"ok": True}
