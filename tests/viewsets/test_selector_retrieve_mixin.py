"""Tests for SelectorRetrieveMixin in isolation (without SelectorListMixin)."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    SelectorKind,
    SelectorRetrieveMixin,
    SelectorRetrieveView,
    SelectorSpec,
    resolve_mutation_instance,
)
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


def _retrieve(*, pk: int, tenant: str) -> Author | None:
    if tenant != "acme":
        return None
    return Author.objects.filter(pk=pk).first()


class _RetrieveOnly(SelectorRetrieveMixin, GenericViewSet):
    action_specs = {"retrieve": SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_retrieve)}
    serializer_class = AuthorSerializer

    def get_selector_kwargs(self) -> dict[str, Any]:
        return {"tenant": "acme"}


factory = APIRequestFactory()


@pytest.mark.django_db
def test_get_selector_kwargs_used_when_mixin_is_alone() -> None:
    author = Author.objects.create(name="Ada")
    view = _RetrieveOnly.as_view({"get": "retrieve"})
    response = view(factory.get("/"), pk=author.pk)
    assert response.status_code == 200
    assert response.data["name"] == "Ada"


@pytest.mark.django_db
def test_default_get_selector_kwargs_returns_empty_dict() -> None:
    instance = SelectorRetrieveMixin()
    assert instance.get_selector_kwargs() == {}


@pytest.mark.django_db
def test_none_resolution_renders_404_by_default() -> None:
    view = _RetrieveOnly.as_view({"get": "retrieve"})
    response = view(factory.get("/"), pk=99999)
    assert response.status_code == 404


def _nullable_retrieve(*, pk: int) -> Author | None:
    return Author.objects.filter(pk=pk).first()


class _NullableRetrieve(SelectorRetrieveMixin, GenericViewSet):
    action_specs = {
        "retrieve": SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_nullable_retrieve, allow_none=True
        ),
    }
    serializer_class = AuthorSerializer


@pytest.mark.django_db
def test_allow_none_renders_200_null() -> None:
    view = _NullableRetrieve.as_view({"get": "retrieve"})
    response = view(factory.get("/"), pk=99999)
    assert response.status_code == 200
    assert response.data is None


@pytest.mark.django_db
def test_allow_none_still_serializes_resolved_instance() -> None:
    author = Author.objects.create(name="Ada")
    view = _NullableRetrieve.as_view({"get": "retrieve"})
    response = view(factory.get("/"), pk=author.pk)
    assert response.status_code == 200
    assert response.data == {"id": author.pk, "name": "Ada"}


class _ExcludeEverything:
    """A filter backend that would leave no row at all."""

    def filter_queryset(self, request: Any, queryset: Any, view: Any) -> Any:
        return queryset.none()


class _BackendRetrieve(SelectorRetrieveMixin, GenericViewSet):
    filter_backends = [_ExcludeEverything]
    action_specs = {
        "retrieve": SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_nullable_retrieve)
    }
    serializer_class = AuthorSerializer
    queryset = Author.objects.all()


@pytest.mark.django_db
def test_filter_backends_do_not_narrow_the_selector_retrieve_lookup() -> None:
    """Characterises the bypass rather than fixing it.

    DRF applies ``filter_queryset()`` from inside its own ``get_object()``, so
    a mixin that overrides that method drops the backends exactly as a
    hand-written override would. Pinned so a change here is a deliberate one.
    """
    author = Author.objects.create(name="Ada")
    view = _BackendRetrieve.as_view({"get": "retrieve"})
    response = view(factory.get("/"), pk=author.pk)
    assert response.status_code == 200


def test_the_three_bypass_sites_disclose_the_dropped_filter_backends() -> None:
    """The disclosure has to live where the bypass does.

    It used to live only in two prose pages, so a reader of ``get_object()``
    learned nothing. This asserts each site still says it, because a
    disclosure nobody is checking is a disclosure that quietly disappears.
    """
    for site in (
        SelectorRetrieveMixin.get_object,
        SelectorRetrieveView.get_object,
        resolve_mutation_instance,
    ):
        doc = site.__doc__ or ""
        assert "filter_backends" in doc, f"{site.__qualname__} does not disclose the bypass"
        assert "filter_queryset" in doc, f"{site.__qualname__} does not name what is skipped"
