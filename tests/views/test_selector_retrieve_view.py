"""Tests for SelectorRetrieveView."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.test import APIRequestFactory

from rest_framework_services import SelectorRetrieveView, SelectorSpec
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


def _get_author(*, pk: int) -> Author | None:
    return Author.objects.filter(pk=pk).first()


class _RetrieveAuthorView(SelectorRetrieveView):
    spec = SelectorSpec(selector=_get_author, output_serializer=AuthorSerializer)


factory = APIRequestFactory()


@pytest.mark.django_db
class TestSelectorRetrieveView:
    def test_returns_serialized_object(self) -> None:
        author = Author.objects.create(name="Ada")
        request = factory.get("/")
        response = _RetrieveAuthorView.as_view()(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data == {"id": author.pk, "name": "Ada"}

    def test_404_when_none_returned(self) -> None:
        request = factory.get("/")
        response = _RetrieveAuthorView.as_view()(request, pk=999)
        assert response.status_code == 404

    def test_404_when_does_not_exist_raised(self) -> None:
        def strict_get(*, pk: int) -> Author:
            return Author.objects.get(pk=pk)

        class _View(SelectorRetrieveView):
            spec = SelectorSpec(selector=strict_get, output_serializer=AuthorSerializer)

        request = factory.get("/")
        response = _View.as_view()(request, pk=999)
        assert response.status_code == 404

    def test_get_selector_kwargs_extras_passed(self) -> None:
        captured: dict[str, Any] = {}

        def tenant_get(*, pk: int, tenant: str) -> Author | None:
            captured.update({"pk": pk, "tenant": tenant})
            return Author.objects.filter(pk=pk).first()

        class _View(SelectorRetrieveView):
            spec = SelectorSpec(selector=tenant_get, output_serializer=AuthorSerializer)

            def get_selector_kwargs(self) -> dict[str, Any]:
                return {"tenant": "acme"}

        author = Author.objects.create(name="x")
        request = factory.get("/")
        _View.as_view()(request, pk=author.pk)
        assert captured == {"pk": author.pk, "tenant": "acme"}

    def test_spec_kwargs_provider_passes_extras(self) -> None:
        captured: dict[str, Any] = {}

        def selector(*, pk: int, tenant_id: int) -> Author | None:
            captured["tenant_id"] = tenant_id
            return Author.objects.filter(pk=pk).first()

        def provider(view: Any, request: Any) -> dict[str, Any]:
            return {"tenant_id": 5}

        class _View(SelectorRetrieveView):
            spec = SelectorSpec(
                selector=selector,
                output_serializer=AuthorSerializer,
                kwargs=provider,
            )

        author = Author.objects.create(name="x")
        _View.as_view()(factory.get("/"), pk=author.pk)
        assert captured["tenant_id"] == 5

    def test_falls_back_to_get_object_when_spec_missing(self) -> None:
        author = Author.objects.create(name="Ada")

        class _Vanilla(SelectorRetrieveView):
            queryset = Author.objects.all()
            serializer_class = AuthorSerializer

        request = factory.get("/")
        response = _Vanilla.as_view()(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data["name"] == "Ada"

    def test_spec_with_no_selector_falls_back_to_get_object(self) -> None:
        author = Author.objects.create(name="Ada")

        class _View(SelectorRetrieveView):
            queryset = Author.objects.all()
            spec = SelectorSpec(output_serializer=AuthorSerializer)

        request = factory.get("/")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data["name"] == "Ada"

    def test_fallback_returns_404_when_object_missing(self) -> None:
        class _Vanilla(SelectorRetrieveView):
            queryset = Author.objects.all()
            serializer_class = AuthorSerializer

        request = factory.get("/")
        response = _Vanilla.as_view()(request, pk=999)
        assert response.status_code == 404
