"""Tests for SelectorViewSet."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.test import APIRequestFactory

from rest_framework_services import SelectorKind, SelectorSpec, SelectorViewSet
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


def _list_authors() -> Any:
    return Author.objects.all().order_by("id")


def _get_author(*, pk: int) -> Author | None:
    return Author.objects.filter(pk=pk).first()


class _AuthorReadOnly(SelectorViewSet):
    action_specs = {
        "list": SelectorSpec(
            kind=SelectorKind.LIST, selector=_list_authors, output_serializer=AuthorSerializer
        ),
        "retrieve": SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_get_author, output_serializer=AuthorSerializer
        ),
    }


factory = APIRequestFactory()


@pytest.mark.django_db
class TestSelectorViewSet:
    def test_list_works(self) -> None:
        Author.objects.create(name="a")
        view = _AuthorReadOnly.as_view({"get": "list"})
        response = view(factory.get("/"))
        assert response.status_code == 200
        assert response.data[0]["name"] == "a"

    def test_retrieve_works(self) -> None:
        a = Author.objects.create(name="b")
        view = _AuthorReadOnly.as_view({"get": "retrieve"})
        response = view(factory.get("/"), pk=a.pk)
        assert response.status_code == 200
        assert response.data["name"] == "b"

    def test_kwargs_hook_passes_extras(self) -> None:
        captured: dict[str, Any] = {}

        def tenant_list(*, tenant: str) -> Any:
            captured["tenant"] = tenant
            return Author.objects.none()

        class _View(SelectorViewSet):
            action_specs = {
                "list": SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=tenant_list,
                    output_serializer=AuthorSerializer,
                ),
            }

            def get_selector_kwargs(self) -> dict[str, Any]:
                return {"tenant": "acme"}

        view = _View.as_view({"get": "list"})
        view(factory.get("/"))
        assert captured["tenant"] == "acme"

    def test_retrieve_404_when_selector_returns_none(self) -> None:
        view = _AuthorReadOnly.as_view({"get": "retrieve"})
        response = view(factory.get("/"), pk=999)
        assert response.status_code == 404

    def test_retrieve_404_when_does_not_exist_raised(self) -> None:
        def strict(*, pk: int) -> Author:
            return Author.objects.get(pk=pk)

        class _View(SelectorViewSet):
            action_specs = {
                "retrieve": SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=strict,
                    output_serializer=AuthorSerializer,
                ),
            }

        view = _View.as_view({"get": "retrieve"})
        response = view(factory.get("/"), pk=999)
        assert response.status_code == 404

    def test_list_falls_back_to_get_queryset(self) -> None:
        Author.objects.create(name="alpha")

        class _Vanilla(SelectorViewSet):
            queryset = Author.objects.all().order_by("id")
            serializer_class = AuthorSerializer

        view = _Vanilla.as_view({"get": "list"})
        response = view(factory.get("/"))
        assert response.status_code == 200
        assert response.data[0]["name"] == "alpha"

    def test_retrieve_falls_back_to_get_object(self) -> None:
        author = Author.objects.create(name="Ada")

        class _Vanilla(SelectorViewSet):
            queryset = Author.objects.all()
            serializer_class = AuthorSerializer

        view = _Vanilla.as_view({"get": "retrieve"})
        response = view(factory.get("/"), pk=author.pk)
        assert response.status_code == 200
        assert response.data["name"] == "Ada"
