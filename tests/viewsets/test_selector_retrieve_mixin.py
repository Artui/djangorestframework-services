"""Tests for SelectorRetrieveMixin in isolation (without SelectorListMixin)."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import SelectorKind, SelectorRetrieveMixin, SelectorSpec
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
            kind=SelectorKind.RETRIEVE, selector=_nullable_retrieve, none_as_404=False
        ),
    }
    serializer_class = AuthorSerializer


@pytest.mark.django_db
def test_none_as_404_false_renders_200_null() -> None:
    view = _NullableRetrieve.as_view({"get": "retrieve"})
    response = view(factory.get("/"), pk=99999)
    assert response.status_code == 200
    assert response.data is None


@pytest.mark.django_db
def test_none_as_404_false_still_serializes_resolved_instance() -> None:
    author = Author.objects.create(name="Ada")
    view = _NullableRetrieve.as_view({"get": "retrieve"})
    response = view(factory.get("/"), pk=author.pk)
    assert response.status_code == 200
    assert response.data == {"id": author.pk, "name": "Ada"}
