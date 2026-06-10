"""Tests for ``ServiceSpec.partial`` and the ``"partial_update"`` action key."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    ActionSerializerResolver,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceUpdateView,
    ServiceViewSet,
    service_action,
)
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

factory = APIRequestFactory()


class _NameInput(serializers.Serializer):
    name = serializers.CharField()  # required


class _DenyAll(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return False


def _update_author(*, instance: Author, data: dict[str, Any]) -> Author:
    instance.name = data.get("name", instance.name)
    instance.save(update_fields=["name"])
    return instance


def _create_author(*, data: dict[str, Any]) -> Author:
    return Author.objects.create(name=data.get("name", "default"))


@pytest.mark.django_db
class TestPartialOverride:
    def test_patch_with_partial_false_enforces_required(self) -> None:
        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "partial_update": ServiceSpec(
                    service=_update_author,
                    input_serializer=_NameInput,
                    partial=False,
                ),
            }

        author = Author.objects.create(name="orig")
        view = _View.as_view({"patch": "partial_update"})
        response = view(factory.patch("/", {}, format="json"), pk=author.pk)
        assert response.status_code == 400
        assert "name" in response.data

    def test_put_with_partial_true_skips_required(self) -> None:
        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "update": ServiceSpec(
                    service=_update_author,
                    input_serializer=_NameInput,
                    partial=True,
                ),
            }

        author = Author.objects.create(name="orig")
        view = _View.as_view({"put": "update"})
        response = view(factory.put("/", {}, format="json"), pk=author.pk)
        assert response.status_code == 200
        author.refresh_from_db()
        assert author.name == "orig"

    def test_partial_none_keeps_method_derived_behaviour(self) -> None:
        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "update": ServiceSpec(service=_update_author, input_serializer=_NameInput),
            }

        author = Author.objects.create(name="orig")
        put_response = _View.as_view({"put": "update"})(
            factory.put("/", {}, format="json"), pk=author.pk
        )
        patch_response = _View.as_view({"patch": "partial_update"})(
            factory.patch("/", {}, format="json"), pk=author.pk
        )
        assert put_response.status_code == 400
        assert patch_response.status_code == 200

    def test_partial_true_applies_to_create_dispatch(self) -> None:
        # ``spec.partial`` is applied at the single dispatch point, so it
        # works uniformly — including create.
        class _View(ServiceViewSet):
            action_specs = {
                "create": ServiceSpec(
                    service=_create_author,
                    input_serializer=_NameInput,
                    partial=True,
                ),
            }

        view = _View.as_view({"post": "create"})
        response = view(factory.post("/", {}, format="json"))
        assert response.status_code == 201
        assert Author.objects.filter(name="default").exists()

    def test_partial_override_on_standalone_view(self) -> None:
        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=_update_author,
                input_serializer=_NameInput,
                partial=False,
            )

        author = Author.objects.create(name="orig")
        response = _View.as_view()(factory.patch("/", {}, format="json"), pk=author.pk)
        assert response.status_code == 400

    def test_partial_override_through_service_action(self) -> None:
        class _ViewSet(GenericViewSet):
            queryset = Author.objects.all()

            @service_action(
                ServiceSpec(
                    service=_update_author,
                    input_serializer=_NameInput,
                    partial=True,
                ),
                detail=True,
                methods=["post"],
            )
            def rename(self, request: Any, pk: Any = None) -> Any: ...

        author = Author.objects.create(name="orig")
        view = _ViewSet.as_view({"post": "rename"})
        response = view(factory.post("/", {}, format="json"), pk=author.pk)
        assert response.status_code == 200


@pytest.mark.django_db
class TestPartialUpdateActionKey:
    def test_partial_update_key_wins_over_update_for_patch(self) -> None:
        ran: list[str] = []

        def _put_service(*, instance: Author) -> None:
            ran.append("update")

        def _patch_service(*, instance: Author) -> None:
            ran.append("partial_update")

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "update": ServiceSpec(service=_put_service),
                "partial_update": ServiceSpec(service=_patch_service),
            }

        author = Author.objects.create(name="x")
        _View.as_view({"patch": "partial_update"})(factory.patch("/"), pk=author.pk)
        _View.as_view({"put": "update"})(factory.put("/"), pk=author.pk)
        assert ran == ["partial_update", "update"]

    def test_patch_falls_back_to_update_key(self) -> None:
        ran: list[str] = []

        def _service(*, instance: Author) -> None:
            ran.append("update")

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {"update": ServiceSpec(service=_service)}

        author = Author.objects.create(name="x")
        response = _View.as_view({"patch": "partial_update"})(factory.patch("/"), pk=author.pk)
        assert response.status_code == 204
        assert ran == ["update"]

    def test_patch_405_when_neither_key_configured(self) -> None:
        def _noop(*, instance: Author) -> None:
            return None

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {"destroy": ServiceSpec(service=_noop)}

        author = Author.objects.create(name="x")
        response = _View.as_view({"patch": "partial_update"})(factory.patch("/"), pk=author.pk)
        assert response.status_code == 405

    def test_only_partial_update_key_makes_put_405(self) -> None:
        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "partial_update": ServiceSpec(service=_update_author, input_serializer=_NameInput),
            }

        author = Author.objects.create(name="orig")
        put_response = _View.as_view({"put": "update"})(
            factory.put("/", {"name": "x"}, format="json"), pk=author.pk
        )
        patch_response = _View.as_view({"patch": "partial_update"})(
            factory.patch("/", {"name": "y"}, format="json"), pk=author.pk
        )
        assert put_response.status_code == 405
        assert patch_response.status_code == 200


@pytest.mark.django_db
class TestPartialUpdatePermissionFallback:
    def test_update_spec_permissions_enforced_on_patch_without_duplicate_key(self) -> None:
        # Regression: permission resolution keys on ``self.action``
        # (``"partial_update"`` under PATCH) while dispatch resolved
        # ``"update"`` — the spec's permissions silently didn't apply to
        # PATCH unless the spec was duplicated under a second key.
        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "update": ServiceSpec(
                    service=_update_author,
                    input_serializer=_NameInput,
                    permission_classes=[_DenyAll],
                ),
            }

        author = Author.objects.create(name="orig")
        response = _View.as_view({"patch": "partial_update"})(
            factory.patch("/", {"name": "x"}, format="json"), pk=author.pk
        )
        assert response.status_code == 403

    def test_partial_update_key_permissions_win_for_patch(self) -> None:
        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "update": ServiceSpec(service=_update_author, input_serializer=_NameInput),
                "partial_update": ServiceSpec(
                    service=_update_author,
                    input_serializer=_NameInput,
                    permission_classes=[_DenyAll],
                ),
            }

        author = Author.objects.create(name="orig")
        patch_response = _View.as_view({"patch": "partial_update"})(
            factory.patch("/", {"name": "x"}, format="json"), pk=author.pk
        )
        put_response = _View.as_view({"put": "update"})(
            factory.put("/", {"name": "x"}, format="json"), pk=author.pk
        )
        assert patch_response.status_code == 403
        assert put_response.status_code == 200


@pytest.mark.django_db
class TestPartialUpdateSerializerResolution:
    def test_serializer_resolution_follows_the_fallback_chain(self) -> None:
        class _View(ActionSerializerResolver, GenericViewSet):
            action_specs = {
                "update": ServiceSpec(
                    service=_update_author,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                    ),
                ),
            }

        view = _View()
        view.action = "partial_update"
        assert view.get_serializer_class() is AuthorSerializer

    def test_dedicated_partial_update_key_wins_for_serializer_resolution(self) -> None:
        class _PatchSerializer(serializers.Serializer):
            name = serializers.CharField()

        class _View(ActionSerializerResolver, GenericViewSet):
            action_specs = {
                "update": ServiceSpec(
                    service=_update_author,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                    ),
                ),
                "partial_update": ServiceSpec(
                    service=_update_author,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE, output_serializer=_PatchSerializer
                    ),
                ),
            }

        view = _View()
        view.action = "partial_update"
        assert view.get_serializer_class() is _PatchSerializer
