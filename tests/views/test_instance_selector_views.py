"""Standalone-view tests for ``ServiceSpec.instance_selector_spec``."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceCreateView,
    ServiceDeleteView,
    ServiceSpec,
    ServiceUpdateView,
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


class _SpecLookupUpdateView(ServiceUpdateView):
    # Deliberately no ``queryset`` / ``lookup_field`` — the spec carries
    # the lookup.
    spec = ServiceSpec(
        service=_update_author,
        input_serializer=_AuthorIn,
        instance_selector_spec=_INSTANCE_SPEC,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
        ),
    )


class _SpecLookupDeleteView(ServiceDeleteView):
    spec = ServiceSpec(service=_delete_author, instance_selector_spec=_INSTANCE_SPEC)


@pytest.mark.django_db
class TestStandaloneInstanceSelector:
    def test_update_view_without_queryset(self) -> None:
        author = Author.objects.create(name="orig")
        response = _SpecLookupUpdateView.as_view()(
            factory.put("/", {"name": "new"}, format="json"), pk=author.pk
        )
        assert response.status_code == 200
        author.refresh_from_db()
        assert author.name == "new"

    def test_patch_on_update_view_without_queryset(self) -> None:
        author = Author.objects.create(name="orig")
        response = _SpecLookupUpdateView.as_view()(
            factory.patch("/", {"name": "new"}, format="json"), pk=author.pk
        )
        assert response.status_code == 200
        author.refresh_from_db()
        assert author.name == "new"

    def test_delete_view_without_queryset(self) -> None:
        author = Author.objects.create(name="x")
        response = _SpecLookupDeleteView.as_view()(factory.delete("/"), pk=author.pk)
        assert response.status_code == 204
        assert not Author.objects.filter(pk=author.pk).exists()

    def test_missing_row_renders_404(self) -> None:
        response = _SpecLookupUpdateView.as_view()(
            factory.put("/", {"name": "new"}, format="json"), pk=99999
        )
        assert response.status_code == 404


class TestInstanceSelectorSpecValidation:
    def test_list_kind_rejected_at_as_view(self) -> None:
        class _View(ServiceUpdateView):
            spec = ServiceSpec(
                service=_update_author,
                input_serializer=_AuthorIn,
                instance_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_author_by_pk),
            )

        with pytest.raises(ImproperlyConfigured, match="must be SelectorKind.RETRIEVE"):
            _View.as_view()

    def test_rejected_on_create_view_at_as_view(self) -> None:
        def _create_author(*, data: _AuthorIn) -> Author:
            return Author.objects.create(name=data.name)

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create_author,
                input_serializer=_AuthorIn,
                instance_selector_spec=_INSTANCE_SPEC,
            )

        with pytest.raises(ImproperlyConfigured, match="does not target an instance"):
            _View.as_view()


@pytest.mark.django_db
class TestNestedOutputSpecIgnoresNoneAs404:
    def test_output_selector_none_still_renders_204(self) -> None:
        # ``none_as_404`` expresses a *standalone read* contract; the nested
        # output spec keeps its authoritative-None → 204 behaviour.
        def _vanishing(*, result: Author) -> None:
            return None

        class _View(ServiceUpdateView):
            spec = ServiceSpec(
                service=_update_author,
                input_serializer=_AuthorIn,
                instance_selector_spec=_INSTANCE_SPEC,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_vanishing,
                    none_as_404=False,
                ),
            )

        author = Author.objects.create(name="orig")
        response = _View.as_view()(factory.put("/", {"name": "new"}, format="json"), pk=author.pk)
        assert response.status_code == 204
