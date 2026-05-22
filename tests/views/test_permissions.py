"""Tests for spec-level permission_classes on standalone views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    SelectorKind,
    SelectorListView,
    SelectorRetrieveView,
    SelectorSpec,
    ServiceCreateView,
    ServiceDeleteView,
    ServiceSpec,
    ServiceUpdateView,
)
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


class _DenyAll(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        return False


@dataclass
class _AuthorIn:
    name: str


def _create_author(*, data: _AuthorIn) -> Author:
    return Author.objects.create(name=data.name)


def _update_author(*, instance: Author, data: _AuthorIn) -> Author:
    instance.name = data.name
    instance.save(update_fields=["name"])
    return instance


def _delete_author(*, instance: Author) -> None:
    instance.delete()


def _list_authors() -> Any:
    return Author.objects.all().order_by("id")


def _get_author(*, pk: int) -> Author | None:
    return Author.objects.filter(pk=pk).first()


factory = APIRequestFactory()


@pytest.mark.django_db
class TestServiceCreateViewPermissions:
    def test_spec_permission_denies(self) -> None:
        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create_author,
                input_serializer=_AuthorIn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
                permission_classes=[_DenyAll],
            )

        response = _View.as_view()(factory.post("/", {"name": "Ada"}, format="json"))
        assert response.status_code == 403

    def test_spec_permission_overrides_view_level(self) -> None:
        class _View(ServiceCreateView):
            permission_classes = [AllowAny]
            spec = ServiceSpec(
                service=_create_author,
                input_serializer=_AuthorIn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
                permission_classes=[_DenyAll],
            )

        response = _View.as_view()(factory.post("/", {"name": "Ada"}, format="json"))
        assert response.status_code == 403

    def test_spec_permission_none_falls_through_to_view(self) -> None:
        class _View(ServiceCreateView):
            permission_classes = [_DenyAll]
            spec = ServiceSpec(
                service=_create_author,
                input_serializer=_AuthorIn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
            )

        response = _View.as_view()(factory.post("/", {"name": "Ada"}, format="json"))
        assert response.status_code == 403

    def test_spec_permission_empty_means_no_permissions(self) -> None:
        class _View(ServiceCreateView):
            permission_classes = [_DenyAll]
            spec = ServiceSpec(
                service=_create_author,
                input_serializer=_AuthorIn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
                permission_classes=[],
            )

        response = _View.as_view()(factory.post("/", {"name": "Ada"}, format="json"))
        assert response.status_code == 201


@pytest.mark.django_db
class TestServiceUpdateViewPermissions:
    def test_spec_permission_denies(self) -> None:
        author = Author.objects.create(name="x")

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=_update_author,
                input_serializer=_AuthorIn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
                permission_classes=[_DenyAll],
            )

        response = _View.as_view()(factory.put("/", {"name": "y"}, format="json"), pk=author.pk)
        assert response.status_code == 403


@pytest.mark.django_db
class TestServiceDeleteViewPermissions:
    def test_spec_permission_denies(self) -> None:
        author = Author.objects.create(name="x")

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=_delete_author,
                permission_classes=[_DenyAll],
            )

        response = _View.as_view()(factory.delete("/"), pk=author.pk)
        assert response.status_code == 403


@pytest.mark.django_db
class TestSelectorListViewPermissions:
    def test_spec_permission_denies(self) -> None:
        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_list_authors,
                output_serializer=AuthorSerializer,
                permission_classes=[_DenyAll],
            )

        response = _View.as_view()(factory.get("/"))
        assert response.status_code == 403

    def test_spec_permission_none_falls_through(self) -> None:
        class _View(SelectorListView):
            permission_classes = [_DenyAll]
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_list_authors,
                output_serializer=AuthorSerializer,
            )

        response = _View.as_view()(factory.get("/"))
        assert response.status_code == 403

    def test_no_spec_falls_through(self) -> None:
        class _View(SelectorListView):
            permission_classes = [_DenyAll]
            queryset = Author.objects.all()
            serializer_class = AuthorSerializer

        response = _View.as_view()(factory.get("/"))
        assert response.status_code == 403


@pytest.mark.django_db
class TestSelectorRetrieveViewPermissions:
    def test_spec_permission_denies(self) -> None:
        author = Author.objects.create(name="x")

        class _View(SelectorRetrieveView):
            spec = SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_get_author,
                output_serializer=AuthorSerializer,
                permission_classes=[_DenyAll],
            )

        response = _View.as_view()(factory.get("/"), pk=author.pk)
        assert response.status_code == 403

    def test_no_spec_falls_through(self) -> None:
        author = Author.objects.create(name="x")

        class _View(SelectorRetrieveView):
            permission_classes = [_DenyAll]
            queryset = Author.objects.all()
            serializer_class = AuthorSerializer

        response = _View.as_view()(factory.get("/"), pk=author.pk)
        assert response.status_code == 403


class TestPermissionClassesValidation:
    def test_service_spec_non_basepermission_raises(self) -> None:
        class _NotAPermission: ...

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create_author,
                input_serializer=_AuthorIn,
                permission_classes=[_NotAPermission],  # type: ignore[list-item]
            )

        with pytest.raises(ImproperlyConfigured, match="permission_classes"):
            _View.as_view()

    def test_service_spec_instance_not_class_raises(self) -> None:
        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create_author,
                input_serializer=_AuthorIn,
                permission_classes=[_DenyAll()],  # type: ignore[list-item]
            )

        with pytest.raises(ImproperlyConfigured, match="permission_classes"):
            _View.as_view()

    def test_selector_spec_non_basepermission_raises(self) -> None:
        class _NotAPermission: ...

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_list_authors,
                permission_classes=[_NotAPermission],  # type: ignore[list-item]
            )

        with pytest.raises(ImproperlyConfigured, match="permission_classes"):
            _View.as_view()
