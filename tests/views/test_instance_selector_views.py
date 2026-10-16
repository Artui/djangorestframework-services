"""Standalone-view tests for ``ServiceSpec.instance_selector_spec``."""

from __future__ import annotations

from dataclasses import dataclass

import django_filters
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
class TestNestedOutputSpecIgnoresAllowNone:
    def test_output_selector_none_still_renders_204(self) -> None:
        # ``allow_none`` expresses a *standalone read* contract; the nested
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
                    allow_none=True,
                ),
            )

        author = Author.objects.create(name="orig")
        response = _View.as_view()(factory.put("/", {"name": "new"}, format="json"), pk=author.pk)
        assert response.status_code == 204


class _NameFilterSet(django_filters.FilterSet):
    """Scoping filter set: keeps only rows whose ``name`` matches ``?scope=``."""

    scope = django_filters.CharFilter(field_name="name")

    class Meta:
        model = Author
        fields = ["scope"]


class _ScopedUpdateView(ServiceUpdateView):
    spec = ServiceSpec(
        service=_update_author,
        input_serializer=_AuthorIn,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_author_by_pk, filter_set=_NameFilterSet
        ),
    )


@pytest.mark.django_db
class TestInstanceSelectorFilterChannel:
    """A ``filter_set`` on the nested lookup narrows the row a mutation reaches.

    That makes it a scoping rule, and a scoping rule has to read the channel
    the client cannot also use: bound to the body, the value being written
    would decide which row is writable. The query string is the filter's only
    source, so a filter name the body also sends is dropped from it.
    """

    def test_the_query_string_is_the_scope(self) -> None:
        author = Author.objects.create(name="orig")

        response = _ScopedUpdateView.as_view()(
            factory.patch("/?scope=orig", {"name": "new"}, format="json"), pk=author.pk
        )

        assert response.status_code == 200
        author.refresh_from_db()
        assert author.name == "new"

    def test_the_body_cannot_widen_the_scope(self) -> None:
        author = Author.objects.create(name="orig")

        response = _ScopedUpdateView.as_view()(
            factory.patch("/?scope=someone-else", {"name": "new", "scope": "orig"}, format="json"),
            pk=author.pk,
        )

        assert response.status_code == 404
        author.refresh_from_db()
        assert author.name == "orig"

    def test_a_filter_named_like_an_input_field_is_refused(self) -> None:
        """A filter name and an input field share one set of argument names, so a
        filter set reading the key the serializer validates is a misconfigured
        spec rather than two channels told apart at request time."""

        class _CollidingFilterSet(django_filters.FilterSet):
            name = django_filters.CharFilter()

            class Meta:
                model = Author
                fields = ["name"]

        class _View(ServiceUpdateView):
            spec = ServiceSpec(
                service=_update_author,
                input_serializer=_AuthorIn,
                instance_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_author_by_pk,
                    filter_set=_CollidingFilterSet,
                ),
            )

        # Refused where the view is wired, since the serializer can be read
        # there; one that cannot is refused by the request
        # (test_a_serializer_unreadable_without_a_request_is_refused_by_the_request).
        with pytest.raises(ImproperlyConfigured, match="also takes as a writable field"):
            _View.as_view()


@pytest.mark.django_db
class TestNonObjectBody:
    def test_a_json_array_body_is_a_400_not_a_500(self) -> None:
        """Only a ``many=True`` spec takes a list. Reaching a single-instance
        spec, one used to raise an ``AttributeError`` deep in the target lookup
        — which DRF re-raises, so the client got a stack trace instead of the
        rejection its input had earned."""
        author = Author.objects.create(name="orig")

        response = _SpecLookupUpdateView.as_view()(
            factory.patch("/", [1, 2], format="json"), pk=author.pk
        )

        assert response.status_code == 400
        author.refresh_from_db()
        assert author.name == "orig"
