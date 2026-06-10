"""End-to-end tests for ``ServiceSpec.instance_selector_spec`` on viewsets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.db.models import IntegerField, QuerySet, Value
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceViewSet,
    service_action,
)
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

factory = APIRequestFactory()


@dataclass
class _AuthorIn:
    name: str


def _author_by_pk(*, pk: int) -> QuerySet[Author]:
    return Author.objects.filter(pk=pk)


def _update_author(*, instance: Author, data: _AuthorIn) -> Author:
    instance.name = data.name
    instance.save(update_fields=["name"])
    return instance


def _delete_author(*, instance: Author) -> None:
    instance.delete()


_INSTANCE_SPEC = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_author_by_pk)
_OUTPUT_SPEC = SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer)


class _SpecResolvedViewSet(ServiceViewSet):
    # Deliberately no ``queryset``: every lookup must come from the spec.
    action_specs = {
        "update": ServiceSpec(
            service=_update_author,
            input_serializer=_AuthorIn,
            instance_selector_spec=_INSTANCE_SPEC,
            output_selector_spec=_OUTPUT_SPEC,
        ),
        "destroy": ServiceSpec(
            service=_delete_author,
            instance_selector_spec=_INSTANCE_SPEC,
        ),
    }


@pytest.mark.django_db
class TestInstanceSelectorResolution:
    def test_update_resolves_instance_from_spec(self) -> None:
        author = Author.objects.create(name="orig")
        view = _SpecResolvedViewSet.as_view({"put": "update"})
        response = view(factory.put("/", {"name": "new"}, format="json"), pk=author.pk)
        assert response.status_code == 200
        author.refresh_from_db()
        assert author.name == "new"

    def test_partial_update_resolves_instance_from_spec(self) -> None:
        author = Author.objects.create(name="orig")
        view = _SpecResolvedViewSet.as_view({"patch": "partial_update"})
        response = view(factory.patch("/", {"name": "patched"}, format="json"), pk=author.pk)
        assert response.status_code == 200
        author.refresh_from_db()
        assert author.name == "patched"

    def test_destroy_resolves_instance_from_spec(self) -> None:
        author = Author.objects.create(name="x")
        view = _SpecResolvedViewSet.as_view({"delete": "destroy"})
        response = view(factory.delete("/"), pk=author.pk)
        assert response.status_code == 204
        assert not Author.objects.filter(pk=author.pk).exists()

    def test_missing_row_renders_404(self) -> None:
        view = _SpecResolvedViewSet.as_view({"put": "update"})
        response = view(factory.put("/", {"name": "new"}, format="json"), pk=99999)
        assert response.status_code == 404

    def test_allow_none_is_ignored_for_instance_resolution(self) -> None:
        nullable_lookup = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_author_by_pk, allow_none=True
        )

        class _View(ServiceViewSet):
            action_specs = {
                "update": ServiceSpec(
                    service=_update_author,
                    input_serializer=_AuthorIn,
                    instance_selector_spec=nullable_lookup,
                ),
            }

        view = _View.as_view({"put": "update"})
        response = view(factory.put("/", {"name": "new"}, format="json"), pk=99999)
        assert response.status_code == 404

    def test_selectorless_instance_spec_falls_back_to_get_object(self) -> None:
        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "update": ServiceSpec(
                    service=_update_author,
                    input_serializer=_AuthorIn,
                    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE),
                ),
            }

        author = Author.objects.create(name="orig")
        view = _View.as_view({"put": "update"})
        response = view(factory.put("/", {"name": "new"}, format="json"), pk=author.pk)
        assert response.status_code == 200
        author.refresh_from_db()
        assert author.name == "new"

    def test_shaping_applied_to_instance_lookup(self) -> None:
        seen: dict[str, Any] = {}

        def _service(*, instance: Author) -> None:
            seen["marker"] = instance.marker

        shaped_spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_author_by_pk,
            annotations={"marker": Value(42, output_field=IntegerField())},
        )

        class _View(ServiceViewSet):
            action_specs = {
                "update": ServiceSpec(service=_service, instance_selector_spec=shaped_spec),
            }

        author = Author.objects.create(name="x")
        view = _View.as_view({"put": "update"})
        response = view(factory.put("/", {}, format="json"), pk=author.pk)
        assert response.status_code == 204
        assert seen["marker"] == 42


@pytest.mark.django_db
class TestInstanceSelectorPrecedence:
    def test_spec_selector_beats_retrieve_action_spec(self) -> None:
        decoy = Author.objects.create(name="decoy")
        target = Author.objects.create(name="target")

        def _decoy_lookup(*, pk: int) -> Author:
            return decoy

        def _target_lookup(*, pk: int) -> QuerySet[Author]:
            return Author.objects.filter(pk=pk)

        class _View(ServiceViewSet):
            action_specs = {
                "retrieve": SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_decoy_lookup,
                    output_serializer=AuthorSerializer,
                ),
                "update": ServiceSpec(
                    service=_update_author,
                    input_serializer=_AuthorIn,
                    instance_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE, selector=_target_lookup
                    ),
                ),
            }

        view = _View.as_view({"put": "update"})
        response = view(factory.put("/", {"name": "renamed"}, format="json"), pk=target.pk)
        assert response.status_code == 200
        target.refresh_from_db()
        decoy.refresh_from_db()
        assert target.name == "renamed"
        assert decoy.name == "decoy"

    def test_retrieve_action_spec_still_beats_drf_default_without_instance_spec(self) -> None:
        decoy = Author.objects.create(name="decoy")
        target = Author.objects.create(name="target")

        def _decoy_lookup(*, pk: int) -> Author:
            return decoy

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "retrieve": SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_decoy_lookup,
                    output_serializer=AuthorSerializer,
                ),
                "update": ServiceSpec(service=_update_author, input_serializer=_AuthorIn),
            }

        view = _View.as_view({"put": "update"})
        response = view(factory.put("/", {"name": "renamed"}, format="json"), pk=target.pk)
        assert response.status_code == 200
        decoy.refresh_from_db()
        target.refresh_from_db()
        assert decoy.name == "renamed"
        assert target.name == "target"


@pytest.mark.django_db
class TestInstanceSelectorObjectPermissions:
    def test_object_permission_denial_renders_403(self) -> None:
        class _DenyObject(BasePermission):
            def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
                return False

        class _View(ServiceViewSet):
            permission_classes = [_DenyObject]
            action_specs = {
                "update": ServiceSpec(
                    service=_update_author,
                    input_serializer=_AuthorIn,
                    instance_selector_spec=_INSTANCE_SPEC,
                ),
            }

        author = Author.objects.create(name="x")
        view = _View.as_view({"put": "update"})
        response = view(factory.put("/", {"name": "new"}, format="json"), pk=author.pk)
        assert response.status_code == 403


@pytest.mark.django_db
class TestInstanceSelectorOnServiceAction:
    def test_detail_action_resolves_instance_from_spec(self) -> None:
        class _ViewSet(GenericViewSet):
            # No ``queryset`` — the spec carries the lookup.
            @service_action(
                ServiceSpec(
                    service=_update_author,
                    input_serializer=_AuthorIn,
                    instance_selector_spec=_INSTANCE_SPEC,
                    output_selector_spec=_OUTPUT_SPEC,
                ),
                detail=True,
                methods=["post"],
            )
            def rename(self, request: Any, pk: Any = None) -> Any: ...

        author = Author.objects.create(name="orig")
        view = _ViewSet.as_view({"post": "rename"})
        response = view(factory.post("/", {"name": "new"}, format="json"), pk=author.pk)
        assert response.status_code == 200
        author.refresh_from_db()
        assert author.name == "new"


def _update_author_from_dict(*, instance: Author, data: dict[str, Any]) -> Author:
    instance.name = data["name"]
    instance.save(update_fields=["name"])
    return instance


@pytest.mark.django_db
class TestInstanceAwareInputValidation:
    def _recording_serializer(self, seen: dict[str, Any]) -> type[serializers.Serializer]:
        class _Input(serializers.Serializer):
            name = serializers.CharField()

            def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
                seen["instance"] = self.instance
                return attrs

        return _Input

    def test_update_serializer_sees_resolved_instance(self) -> None:
        seen: dict[str, Any] = {}

        class _View(ServiceViewSet):
            action_specs = {
                "update": ServiceSpec(
                    service=_update_author_from_dict,
                    input_serializer=self._recording_serializer(seen),
                    instance_selector_spec=_INSTANCE_SPEC,
                ),
            }

        author = Author.objects.create(name="orig")
        view = _View.as_view({"put": "update"})
        response = view(factory.put("/", {"name": "new"}, format="json"), pk=author.pk)
        assert response.status_code == 200
        assert seen["instance"] == author

    def test_partial_update_serializer_sees_legacy_get_object_instance(self) -> None:
        # The instance-aware construction applies to the legacy
        # ``get_object()`` chain too, not only spec-resolved lookups.
        seen: dict[str, Any] = {}

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "update": ServiceSpec(
                    service=_update_author_from_dict,
                    input_serializer=self._recording_serializer(seen),
                ),
            }

        author = Author.objects.create(name="orig")
        view = _View.as_view({"patch": "partial_update"})
        response = view(factory.patch("/", {"name": "new"}, format="json"), pk=author.pk)
        assert response.status_code == 200
        assert seen["instance"] == author

    def test_create_serializer_still_validates_with_no_instance(self) -> None:
        seen: dict[str, Any] = {}

        def _create_author(*, data: dict[str, Any]) -> Author:
            return Author.objects.create(name=data["name"])

        class _View(ServiceViewSet):
            action_specs = {
                "create": ServiceSpec(
                    service=_create_author,
                    input_serializer=self._recording_serializer(seen),
                ),
            }

        view = _View.as_view({"post": "create"})
        response = view(factory.post("/", {"name": "new"}, format="json"))
        assert response.status_code == 201
        assert seen["instance"] is None


@pytest.mark.django_db
class TestInputDataProviderInstanceExtra:
    def test_provider_declaring_instance_receives_it(self) -> None:
        def _stamp_name(view: Any, request: Any, *, instance: Author) -> dict[str, Any]:
            return {"name": f"{instance.name}-stamped"}

        class _View(ServiceViewSet):
            action_specs = {
                "update": ServiceSpec(
                    service=_update_author,
                    input_serializer=_AuthorIn,
                    instance_selector_spec=_INSTANCE_SPEC,
                    input_data=_stamp_name,
                ),
            }

        author = Author.objects.create(name="orig")
        view = _View.as_view({"put": "update"})
        response = view(factory.put("/", {"name": "ignored"}, format="json"), pk=author.pk)
        assert response.status_code == 200
        author.refresh_from_db()
        assert author.name == "orig-stamped"

    def test_provider_without_instance_is_unaffected(self) -> None:
        def _legacy(view: Any, request: Any) -> dict[str, Any]:
            return {"name": "from-provider"}

        class _View(ServiceViewSet):
            action_specs = {
                "update": ServiceSpec(
                    service=_update_author,
                    input_serializer=_AuthorIn,
                    instance_selector_spec=_INSTANCE_SPEC,
                    input_data=_legacy,
                ),
            }

        author = Author.objects.create(name="orig")
        view = _View.as_view({"put": "update"})
        response = view(factory.put("/", {"name": "ignored"}, format="json"), pk=author.pk)
        assert response.status_code == 200
        author.refresh_from_db()
        assert author.name == "from-provider"
