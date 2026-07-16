"""Integration: ``ServiceSpec.response_finalizer`` across the HTTP flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceCreateView,
    ServiceDeleteView,
    ServiceError,
    ServiceSpec,
    delete_collection,
    dispatch_spec,
    service_action,
)
from tests.testapp.models import Author, Post
from tests.testapp.serializers import AuthorSerializer

factory = APIRequestFactory()


@dataclass
class _AuthorIn:
    name: str


def _create_author(*, data: _AuthorIn) -> Author:
    return Author.objects.create(name=data.name)


def _delete_author(*, instance: Author) -> None:
    instance.delete()


def _set_cookie(*, response: Response) -> None:
    # Returns None → the same response is kept, now carrying the cookie.
    response.set_cookie("session", "abc")
    return None


@pytest.mark.django_db
class TestResponseFinalizerSingle:
    def test_finalizer_sets_cookie_on_serialized_body(self) -> None:
        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create_author,
                input_serializer=_AuthorIn,
                response_finalizer=_set_cookie,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
            )

        response = _View.as_view()(factory.post("/", {"name": "Ada"}, format="json"))
        assert response.status_code == 201
        assert response.data["name"] == "Ada"
        assert response.cookies["session"].value == "abc"

    def test_finalizer_applies_on_empty_body(self) -> None:
        author = Author.objects.create(name="Gone")

        def add_header(*, response: Response) -> None:
            response["X-Deleted"] = "1"
            return None

        class _View(ServiceDeleteView):
            queryset = Author.objects.all()
            spec = ServiceSpec(service=_delete_author, response_finalizer=add_header)

        response = _View.as_view()(factory.delete("/"), pk=author.pk)
        assert response.status_code == 204
        assert response["X-Deleted"] == "1"

    def test_finalizer_can_swap_the_response(self) -> None:
        def swap(*, result: Author, response: Response) -> Response:
            return Response({"id": result.pk, "swapped": True}, status=response.status_code)

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create_author,
                input_serializer=_AuthorIn,
                response_finalizer=swap,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
            )

        response = _View.as_view()(factory.post("/", {"name": "Ada"}, format="json"))
        assert response.status_code == 201
        assert response.data == {"id": Author.objects.get().pk, "swapped": True}

    def test_result_is_the_service_return_not_the_refetched_instance(self) -> None:
        # A finalizer keying on the service's raw return sees the DTO the service
        # produced, even when an output selector re-fetches a different object.
        @dataclass
        class _Made:
            author: Author
            created: bool

        def _make(*, data: _AuthorIn) -> _Made:
            return _Made(author=Author.objects.create(name=data.name), created=True)

        seen: dict[str, Any] = {}

        def record(*, result: _Made, response: Response) -> None:
            seen["created"] = result.created
            return None

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_make,
                input_serializer=_AuthorIn,
                response_finalizer=record,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=lambda *, result: Author.objects.filter(pk=result.author.pk),
                    output_serializer=AuthorSerializer,
                ),
            )

        _View.as_view()(factory.post("/", {"name": "Ada"}, format="json"))
        assert seen["created"] is True

    def test_error_path_bypasses_finalizer(self) -> None:
        called: list[bool] = []

        def boom(*, data: _AuthorIn) -> None:
            raise ServiceError("nope")

        def finalizer(**_: Any) -> None:
            called.append(True)
            return None

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=boom,
                input_serializer=_AuthorIn,
                response_finalizer=finalizer,
                atomic=False,
            )

        response = _View.as_view()(factory.post("/", {"name": "x"}, format="json"))
        assert response.status_code == 422
        assert called == []


@pytest.mark.django_db
class TestResponseFinalizerServiceAction:
    def test_finalizer_rides_along_on_service_action(self) -> None:
        class _ViewSet(GenericViewSet):
            queryset = Author.objects.all()

            @service_action(
                ServiceSpec(
                    service=_create_author,
                    input_serializer=_AuthorIn,
                    response_finalizer=_set_cookie,
                ),
                detail=False,
                methods=["post"],
            )
            def make(self, request):  # type: ignore[no-untyped-def]
                """Stubbed."""

        response = _ViewSet.as_view({"post": "make"})(
            factory.post("/", {"name": "Ada"}, format="json")
        )
        assert response.status_code == 200
        assert response.cookies["session"].value == "abc"


@pytest.mark.django_db
class TestResponseFinalizerBulk:
    def test_finalizer_on_bulk_body(self) -> None:
        def _bulk_create(*, data: list[_AuthorIn]) -> list[Author]:
            return [Author.objects.create(name=item.name) for item in data]

        def tag_header(*, response: Response, result: Any) -> None:
            response["X-Count"] = str(len(result))
            return None

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_bulk_create,
                input_serializer=_AuthorIn,
                many=True,
                response_finalizer=tag_header,
                atomic=False,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
            )

        response = _View.as_view()(factory.post("/", [{"name": "a"}, {"name": "b"}], format="json"))
        assert response.status_code == 201
        assert response["X-Count"] == "2"

    def test_finalizer_on_bulk_empty_body(self) -> None:
        Post.objects.create(title="x")

        def add_header(*, response: Response) -> None:
            response["X-Bulk"] = "done"
            return None

        class _View(ServiceDeleteView):
            spec = ServiceSpec(
                service=delete_collection(Post),
                collection_selector_spec=SelectorSpec(
                    kind=SelectorKind.LIST, selector=lambda: Post.objects.all()
                ),
                response_finalizer=add_header,
                atomic=False,
            )

        response = _View.as_view()(factory.delete("/"))
        assert response.status_code == 204
        assert response["X-Bulk"] == "done"


@pytest.mark.django_db
class TestResponseFinalizerTransportNeutral:
    def test_finalizer_is_skipped_off_http(self) -> None:
        called: list[bool] = []

        def finalizer(**_: Any) -> None:
            called.append(True)
            return None

        spec: ServiceSpec = ServiceSpec(
            service=_create_author,
            input_serializer=_AuthorIn,
            response_finalizer=finalizer,
            atomic=False,
        )
        result = dispatch_spec(spec, user=None, params={"name": "Ada"})
        assert result.value.name == "Ada"
        # dispatch_spec builds no Response, so the finalizer never runs.
        assert called == []
