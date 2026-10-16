"""A spec's serializer context callable runs once per request, on every view path.

The callables are documented as the place for one batched query against the
data being serialized, so a second run is a second query, and a run per row is
N of them. Each HTTP path is driven here with a counting callable: the selector
views and viewsets, ``@selector_action``, the mutation views and viewsets,
``@service_action``, and the bulk paths. Lists are rendered by DRF's own list
serializer or ``render_spec_output`` with the whole set, never row by row, so a
list of two rows still counts one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    SelectorKind,
    SelectorListView,
    SelectorRetrieveView,
    SelectorSpec,
    SelectorViewSet,
    ServiceCreateView,
    ServiceSpec,
    ServiceUpdateView,
    ServiceViewSet,
    selector_action,
    service_action,
)
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

factory = APIRequestFactory()


def _counter() -> tuple[list[int], Callable[..., dict[str, Any]]]:
    calls: list[int] = []

    def context(**_: Any) -> dict[str, Any]:
        calls.append(1)
        return {"counted": True}

    return calls, context


def _authors() -> QuerySet[Author]:
    return Author.objects.order_by("id")


def _author(*, pk: int) -> Author | None:
    return Author.objects.filter(pk=pk).first()


@dataclass
class _AuthorIn:
    name: str


class _NameIn(serializers.Serializer[Any]):
    name = serializers.CharField()


def _create(*, data: _AuthorIn) -> Author:
    return Author.objects.create(name=data.name)


def _create_many(*, data: list[_AuthorIn]) -> list[Author]:
    return [Author.objects.create(name=row.name) for row in data]


def _rename(*, instance: Author, data: _AuthorIn) -> Author:
    instance.name = data.name
    instance.save(update_fields=["name"])
    return instance


def _rename_from_dict(*, instance: Author, data: dict[str, Any]) -> Author:
    instance.name = data["name"]
    instance.save(update_fields=["name"])
    return instance


def _touch_all(*, collection: QuerySet[Author]) -> QuerySet[Author]:
    return collection


@pytest.fixture
def two_authors() -> list[Author]:
    return [Author.objects.create(name="a"), Author.objects.create(name="b")]


# --- output: selector paths ---------------------------------------------------


@pytest.mark.django_db
def test_a_selector_list_view(two_authors: list[Author]) -> None:
    calls, context = _counter()

    class _View(SelectorListView):
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_authors,
            output_serializer=AuthorSerializer,
            output_serializer_context=context,
        )

    assert _View.as_view()(factory.get("/")).status_code == 200
    assert len(calls) == 1


@pytest.mark.django_db
def test_a_selector_retrieve_view(two_authors: list[Author]) -> None:
    calls, context = _counter()

    class _View(SelectorRetrieveView):
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_author,
            output_serializer=AuthorSerializer,
            output_serializer_context=context,
        )

    assert _View.as_view()(factory.get("/"), pk=two_authors[0].pk).status_code == 200
    assert len(calls) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["list", "retrieve"])
def test_a_selector_viewset(two_authors: list[Author], action: str) -> None:
    calls, context = _counter()

    class _View(SelectorViewSet):
        serializer_class = AuthorSerializer
        action_specs = {
            "list": SelectorSpec(
                kind=SelectorKind.LIST, selector=_authors, output_serializer_context=context
            ),
            "retrieve": SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_author, output_serializer_context=context
            ),
        }

    view = _View.as_view({"get": action})
    response = (
        view(factory.get("/"), pk=two_authors[0].pk)
        if action == "retrieve"
        else view(factory.get("/"))
    )
    assert response.status_code == 200
    assert len(calls) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["roster", "card"])
def test_a_selector_action(two_authors: list[Author], action: str) -> None:
    calls, context = _counter()

    class _View(GenericViewSet):
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

        @selector_action(
            SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_authors,
                output_serializer=AuthorSerializer,
                output_serializer_context=context,
            )
        )
        def roster(self, request: Any) -> Any:
            """Stubbed."""

        @selector_action(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_author,
                output_serializer=AuthorSerializer,
                output_serializer_context=context,
            )
        )
        def card(self, request: Any, pk: Any = None) -> Any:
            """Stubbed."""

    view = _View.as_view({"get": action})
    response = (
        view(factory.get("/"), pk=two_authors[0].pk) if action == "card" else view(factory.get("/"))
    )
    assert response.status_code == 200
    assert len(calls) == 1


# --- output: mutation paths ---------------------------------------------------


def _retrieve_output(context: Callable[..., Any]) -> SelectorSpec[Any, Any]:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        output_serializer=AuthorSerializer,
        output_serializer_context=context,
    )


@pytest.mark.django_db
def test_a_create_view() -> None:
    calls, context = _counter()

    class _View(ServiceCreateView):
        spec = ServiceSpec(
            service=_create,
            input_serializer=_AuthorIn,
            output_selector_spec=_retrieve_output(context),
        )

    response = _View.as_view()(factory.post("/", {"name": "n"}, format="json"))
    assert response.status_code == 201
    assert len(calls) == 1


@pytest.mark.django_db
def test_an_update_viewset_action(two_authors: list[Author]) -> None:
    calls, context = _counter()

    class _View(ServiceViewSet):
        queryset = Author.objects.all()
        serializer_class = AuthorSerializer
        action_specs = {
            "update": ServiceSpec(
                service=_rename,
                input_serializer=_AuthorIn,
                output_selector_spec=_retrieve_output(context),
            )
        }

    response = _View.as_view({"put": "update"})(
        factory.put("/", {"name": "n"}, format="json"), pk=two_authors[0].pk
    )
    assert response.status_code == 200
    assert len(calls) == 1


@pytest.mark.django_db
def test_a_service_action(two_authors: list[Author]) -> None:
    calls, context = _counter()

    class _View(GenericViewSet):
        queryset = Author.objects.all()
        serializer_class = AuthorSerializer

        @service_action(
            ServiceSpec(
                service=_rename,
                input_serializer=_AuthorIn,
                output_selector_spec=_retrieve_output(context),
            ),
            detail=True,
            methods=["post"],
        )
        def rename(self, request: Any, pk: Any = None) -> Any:
            """Stubbed."""

    response = _View.as_view({"post": "rename"})(
        factory.post("/", {"name": "n"}, format="json"), pk=two_authors[0].pk
    )
    assert response.status_code == 200
    assert len(calls) == 1


@pytest.mark.django_db
def test_a_many_create_renders_its_rows_with_one_context() -> None:
    calls, context = _counter()

    class _View(ServiceCreateView):
        spec = ServiceSpec(
            service=_create_many,
            input_serializer=_AuthorIn,
            many=True,
            atomic=False,
            output_selector_spec=_retrieve_output(context),
        )

    response = _View.as_view()(factory.post("/", [{"name": "a"}, {"name": "b"}], format="json"))
    assert response.status_code == 201
    assert len(calls) == 1


@pytest.mark.django_db
def test_a_collection_update_renders_its_set_with_one_context(two_authors: list[Author]) -> None:
    calls, context = _counter()

    class _View(ServiceUpdateView):
        spec = ServiceSpec(
            service=_touch_all,
            collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_authors),
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST,
                selector=lambda *, result: result,
                output_serializer=AuthorSerializer,
                output_serializer_context=context,
            ),
            atomic=False,
        )

    response = _View.as_view()(factory.put("/"))
    assert response.status_code == 200
    assert len(response.data) == 2
    assert len(calls) == 1


# --- input ----------------------------------------------------------------------


@pytest.mark.django_db
def test_a_create_views_input_context() -> None:
    calls, context = _counter()

    class _View(ServiceCreateView):
        spec = ServiceSpec(
            service=_create, input_serializer=_AuthorIn, input_serializer_context=context
        )

    response = _View.as_view()(factory.post("/", {"name": "n"}, format="json"))
    assert response.status_code == 201
    assert len(calls) == 1


@pytest.mark.django_db
def test_a_viewset_actions_input_context(two_authors: list[Author]) -> None:
    calls, context = _counter()

    class _View(ServiceViewSet):
        queryset = Author.objects.all()
        serializer_class = AuthorSerializer
        action_specs = {
            "partial_update": ServiceSpec(
                service=_rename_from_dict,
                input_serializer=_NameIn,
                input_serializer_context=context,
            )
        }

    response = _View.as_view({"patch": "partial_update"})(
        factory.patch("/", {"name": "n"}, format="json"), pk=two_authors[0].pk
    )
    assert response.status_code == 200
    assert len(calls) == 1


@pytest.mark.django_db
def test_a_many_creates_input_context_runs_once_for_its_rows() -> None:
    calls, context = _counter()

    class _View(ServiceCreateView):
        spec = ServiceSpec(
            service=_create_many,
            input_serializer=_AuthorIn,
            many=True,
            atomic=False,
            input_serializer_context=context,
        )

    response = _View.as_view()(factory.post("/", [{"name": "a"}, {"name": "b"}], format="json"))
    assert response.status_code == 201
    assert len(calls) == 1
