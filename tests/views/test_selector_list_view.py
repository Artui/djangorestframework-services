"""Tests for SelectorListView."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.pagination import PageNumberPagination
from rest_framework.test import APIRequestFactory

from rest_framework_services import SelectorKind, SelectorListView, SelectorSpec
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


def _list_authors() -> Any:
    return Author.objects.all().order_by("id")


class _ListAuthorsView(SelectorListView):
    spec = SelectorSpec(
        kind=SelectorKind.LIST, selector=_list_authors, output_serializer=AuthorSerializer
    )


factory = APIRequestFactory()


@pytest.mark.django_db
class TestSelectorListView:
    def test_returns_serialized_queryset(self) -> None:
        Author.objects.create(name="a")
        Author.objects.create(name="b")
        request = factory.get("/")
        response = _ListAuthorsView.as_view()(request)
        assert response.status_code == 200
        assert [item["name"] for item in response.data] == ["a", "b"]

    def test_pagination_engaged(self) -> None:
        for i in range(5):
            Author.objects.create(name=f"a{i}")

        class _Paginator(PageNumberPagination):
            page_size = 2

        class _Paginated(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST, selector=_list_authors, output_serializer=AuthorSerializer
            )
            pagination_class = _Paginator

        request = factory.get("/?page=1")
        response = _Paginated.as_view()(request)
        assert response.status_code == 200
        assert len(response.data["results"]) == 2
        assert response.data["count"] == 5

    def test_get_selector_kwargs_extras_passed(self) -> None:
        captured: dict[str, Any] = {}

        def tenant_selector(*, tenant: str) -> Any:
            captured["tenant"] = tenant
            return Author.objects.none()

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=tenant_selector,
                output_serializer=AuthorSerializer,
            )

            def get_selector_kwargs(self) -> dict[str, Any]:
                return {"tenant": "acme"}

        request = factory.get("/")
        _View.as_view()(request)
        assert captured["tenant"] == "acme"

    def test_spec_kwargs_provider_passes_extras(self) -> None:
        captured: dict[str, Any] = {}

        def selector(*, tenant_id: int) -> Any:
            captured["tenant_id"] = tenant_id
            return Author.objects.none()

        def provider(view: Any, request: Any) -> dict[str, Any]:
            return {"tenant_id": 12}

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=selector,
                output_serializer=AuthorSerializer,
                kwargs=provider,
            )

        _View.as_view()(factory.get("/"))
        assert captured["tenant_id"] == 12

    def test_url_kwargs_available_to_selector(self) -> None:
        captured: dict[str, Any] = {}

        def by_author(*, author_id: int) -> Any:
            captured["author_id"] = author_id
            return Author.objects.filter(pk=author_id).order_by("id")

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=by_author,
                output_serializer=AuthorSerializer,
            )

        author = Author.objects.create(name="x")
        request = factory.get("/")
        response = _View.as_view()(request, author_id=author.pk)
        assert response.status_code == 200
        assert captured["author_id"] == author.pk

    def test_falls_back_to_get_queryset_when_spec_missing(self) -> None:
        Author.objects.create(name="alpha")
        Author.objects.create(name="beta")

        class _Vanilla(SelectorListView):
            queryset = Author.objects.all().order_by("id")
            serializer_class = AuthorSerializer

        request = factory.get("/")
        response = _Vanilla.as_view()(request)
        assert response.status_code == 200
        assert [a["name"] for a in response.data] == ["alpha", "beta"]

    def test_spec_with_no_selector_falls_back_to_get_queryset(self) -> None:
        Author.objects.create(name="alpha")

        class _View(SelectorListView):
            queryset = Author.objects.all().order_by("id")
            spec = SelectorSpec(kind=SelectorKind.LIST, output_serializer=AuthorSerializer)

        request = factory.get("/")
        response = _View.as_view()(request)
        assert response.status_code == 200
        assert response.data[0]["name"] == "alpha"

    def test_filter_backends_apply_to_selector_queryset(self) -> None:
        """Filter backends still wrap whatever ``get_queryset`` returns —
        whether it came from the selector or from DRF's default."""
        Author.objects.create(name="alpha")
        Author.objects.create(name="beta")

        class _OnlyAlpha:
            def filter_queryset(self, request: Any, queryset: Any, view: Any) -> Any:
                return queryset.filter(name="alpha")

        class _Filtered(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST, selector=_list_authors, output_serializer=AuthorSerializer
            )
            filter_backends = (_OnlyAlpha,)

        request = factory.get("/")
        response = _Filtered.as_view()(request)
        assert [a["name"] for a in response.data] == ["alpha"]

    def test_list_selector_object_does_not_exist_propagates(self) -> None:
        """LIST kind re-raises ObjectDoesNotExist (only RETRIEVE maps to 404)."""
        from django.core.exceptions import ObjectDoesNotExist

        def boom(**_: Any) -> Any:
            raise ObjectDoesNotExist()

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=boom,
                output_serializer=AuthorSerializer,
            )

        with pytest.raises(ObjectDoesNotExist):
            _View.as_view()(factory.get("/"))
