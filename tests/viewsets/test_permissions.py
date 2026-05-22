"""Tests for spec-level permission_classes on viewsets and decorators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    SelectorViewSet,
    ServiceSpec,
    ServiceViewSet,
    selector_action,
    service_action,
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


def _list_authors() -> Any:
    return Author.objects.all().order_by("id")


factory = APIRequestFactory()


@pytest.mark.django_db
class TestActionSpecsMixinPermissions:
    def test_spec_permission_denies_for_action(self) -> None:
        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "create": ServiceSpec(
                    service=_create_author,
                    input_serializer=_AuthorIn,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE,
                        output_serializer=AuthorSerializer,
                    ),
                    permission_classes=[_DenyAll],
                ),
            }

        response = _View.as_view({"post": "create"})(
            factory.post("/", {"name": "Ada"}, format="json"),
        )
        assert response.status_code == 403

    def test_spec_permission_per_action(self) -> None:
        """list is open; create is denied — same viewset."""

        class _View(ServiceViewSet):
            queryset = Author.objects.all()
            action_specs = {
                "list": SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_list_authors,
                    output_serializer=AuthorSerializer,
                    permission_classes=[AllowAny],
                ),
                "create": ServiceSpec(
                    service=_create_author,
                    input_serializer=_AuthorIn,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE,
                        output_serializer=AuthorSerializer,
                    ),
                    permission_classes=[_DenyAll],
                ),
            }

        Author.objects.create(name="a")
        list_resp = _View.as_view({"get": "list"})(factory.get("/"))
        assert list_resp.status_code == 200

        create_resp = _View.as_view({"post": "create"})(
            factory.post("/", {"name": "b"}, format="json"),
        )
        assert create_resp.status_code == 403

    def test_spec_without_permission_classes_inherits_view_level(self) -> None:
        class _View(ServiceViewSet):
            permission_classes = [_DenyAll]
            queryset = Author.objects.all()
            action_specs = {
                "create": ServiceSpec(
                    service=_create_author,
                    input_serializer=_AuthorIn,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE,
                        output_serializer=AuthorSerializer,
                    ),
                ),
            }

        response = _View.as_view({"post": "create"})(
            factory.post("/", {"name": "Ada"}, format="json"),
        )
        assert response.status_code == 403

    def test_no_spec_for_action_inherits_view_level(self) -> None:
        """Custom @action that isn't in action_specs uses view-level permissions."""

        class _View(ServiceViewSet):
            permission_classes = [_DenyAll]
            queryset = Author.objects.all()
            action_specs = {}

            @service_action(ServiceSpec(service=staticmethod(lambda: {"ok": True})))
            def custom(self, request: Any, *args: Any, **kwargs: Any) -> Response: ...

        # custom action — no spec in action_specs, no permission on @service_action,
        # so the view-level _DenyAll governs.
        response = _View.as_view({"post": "custom"})(factory.post("/", {}, format="json"))
        assert response.status_code == 403


@pytest.mark.django_db
class TestServiceActionDecoratorPermissions:
    """The decorators forward ``spec.permission_classes`` to DRF's ``@action``,
    which the router applies via ``as_view(..., permission_classes=...)``.
    Viewsets that compose ``_ActionSpecsMixin`` (i.e. ``ServiceViewSet`` /
    ``SelectorViewSet``) additionally honor the spec attached to the bound
    handler so the decorator works without requiring the router.
    """

    def test_spec_permission_denies_with_service_viewset(self) -> None:
        class _View(ServiceViewSet):
            queryset = Author.objects.all()

            @service_action(
                ServiceSpec(
                    service=staticmethod(lambda: {"ok": True}),
                    permission_classes=[_DenyAll],
                )
            )
            def custom(self, request: Any, *args: Any, **kwargs: Any) -> Response: ...

        response = _View.as_view({"post": "custom"})(factory.post("/", {}, format="json"))
        assert response.status_code == 403

    def test_spec_permission_allows_overrides_view_default(self) -> None:
        class _View(ServiceViewSet):
            permission_classes = [_DenyAll]
            queryset = Author.objects.all()

            @service_action(
                ServiceSpec(
                    service=staticmethod(lambda: {"ok": True}),
                    permission_classes=[AllowAny],
                )
            )
            def custom(self, request: Any, *args: Any, **kwargs: Any) -> Response: ...

        response = _View.as_view({"post": "custom"})(factory.post("/", {}, format="json"))
        assert response.status_code == 200

    def test_decorator_forwards_to_drf_action_kwargs(self) -> None:
        """The decorator stuffs permission_classes into DRF's action kwargs
        so the router will pick them up at URL-resolution time."""

        class _View(GenericViewSet):
            queryset = Author.objects.all()

            @service_action(
                ServiceSpec(
                    service=staticmethod(lambda: {"ok": True}),
                    permission_classes=[_DenyAll],
                )
            )
            def custom(self, request: Any, *args: Any, **kwargs: Any) -> Response: ...

        # DRF's @action stamps the kwargs (including permission_classes) onto
        # the wrapped function. The router copies them into initkwargs for
        # as_view(...).
        assert _View.custom.kwargs["permission_classes"] == [_DenyAll]  # type: ignore[attr-defined]


@pytest.mark.django_db
class TestSelectorActionDecoratorPermissions:
    def test_spec_permission_denies_with_selector_viewset(self) -> None:
        class _View(SelectorViewSet):
            queryset = Author.objects.all()

            @selector_action(
                SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_list_authors,
                    output_serializer=AuthorSerializer,
                    permission_classes=[_DenyAll],
                )
            )
            def custom(self, request: Any, *args: Any, **kwargs: Any) -> Response: ...

        response = _View.as_view({"get": "custom"})(factory.get("/"))
        assert response.status_code == 403

    def test_spec_permission_allows_overrides_view_default(self) -> None:
        class _View(SelectorViewSet):
            permission_classes = [_DenyAll]
            queryset = Author.objects.all()

            @selector_action(
                SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_list_authors,
                    output_serializer=AuthorSerializer,
                    permission_classes=[AllowAny],
                )
            )
            def custom(self, request: Any, *args: Any, **kwargs: Any) -> Response: ...

        response = _View.as_view({"get": "custom"})(factory.get("/"))
        assert response.status_code == 200

    def test_decorator_forwards_to_drf_action_kwargs(self) -> None:
        class _View(GenericViewSet):
            queryset = Author.objects.all()

            @selector_action(
                SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_list_authors,
                    output_serializer=AuthorSerializer,
                    permission_classes=[_DenyAll],
                )
            )
            def custom(self, request: Any, *args: Any, **kwargs: Any) -> Response: ...

        assert _View.custom.kwargs["permission_classes"] == [_DenyAll]  # type: ignore[attr-defined]
