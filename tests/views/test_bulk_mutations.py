"""Bulk mutations through the HTTP views (many + collection targets)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceCreateView,
    ServiceDeleteView,
    ServiceError,
    ServiceSpec,
    ServiceUpdateView,
    delete_collection,
)
from tests.testapp.models import Author, Post

factory = APIRequestFactory()


@dataclass
class _PostIn:
    title: str


class _PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ("id", "title")


class _PublishedFilterSet:
    def __init__(self, *, data: Any, queryset: QuerySet[Post]) -> None:
        self._data = data
        self._queryset = queryset

    @property
    def qs(self) -> QuerySet[Post]:
        raw = self._data.get("published")
        if raw is None:
            return self._queryset
        return self._queryset.filter(published=str(raw).lower() in ("1", "true", "yes"))


def _all_posts() -> QuerySet[Post]:
    return Post.objects.all().order_by("id")


def _bulk_create(*, data: list[_PostIn]) -> list[Post]:
    return [Post.objects.create(title=item.title) for item in data]


@pytest.mark.django_db
class TestBulkCreateMany:
    def test_list_in_list_out_201(self) -> None:
        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_bulk_create,
                input_serializer=_PostIn,
                many=True,
                atomic=False,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=_PostSerializer
                ),
            )

        response = _View.as_view()(
            factory.post("/", [{"title": "a"}, {"title": "b"}], format="json")
        )
        assert response.status_code == 201
        assert [row["title"] for row in response.data] == ["a", "b"]

    def test_callable_success_status_on_bulk_body(self) -> None:
        def status(*, result: list[Post]) -> int:
            return 201 if result else 200

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_bulk_create,
                input_serializer=_PostIn,
                many=True,
                success_status=status,
                atomic=False,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=_PostSerializer
                ),
            )

        response = _View.as_view()(factory.post("/", [{"title": "a"}], format="json"))
        assert response.status_code == 201


@pytest.mark.django_db
class TestBulkDeleteCollection:
    def test_callable_success_status_empty_body(self) -> None:
        Post.objects.create(title="a")

        def status(*, result: Any) -> int:
            return 205

        class _View(ServiceDeleteView):
            spec = ServiceSpec(
                service=delete_collection(Post),
                collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts),
                success_status=status,
                atomic=False,
            )

        response = _View.as_view()(factory.delete("/"))
        assert response.status_code == 205

    def test_delete_over_query_filter_204(self) -> None:
        Post.objects.create(title="shipped", published=True)
        Post.objects.create(title="draft", published=False)

        class _View(ServiceDeleteView):
            spec = ServiceSpec(
                service=delete_collection(Post),
                collection_selector_spec=SelectorSpec(
                    kind=SelectorKind.LIST, selector=_all_posts, filter_set=_PublishedFilterSet
                ),
                atomic=False,
            )

        response = _View.as_view()(factory.delete("/?published=true"))
        assert response.status_code == 204
        assert set(Post.objects.values_list("title", flat=True)) == {"draft"}

    def test_count_return_bumps_204_to_200(self) -> None:
        Post.objects.create(title="a")
        Post.objects.create(title="b")

        def count_delete(*, collection: QuerySet[Post]) -> dict[str, int]:
            n, _ = collection.delete()
            return {"deleted": n}

        class _View(ServiceDeleteView):
            spec = ServiceSpec(
                service=count_delete,
                collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts),
                atomic=False,
            )

        response = _View.as_view()(factory.delete("/"))
        assert response.status_code == 200
        assert response.data == {"deleted": 2}

    def test_service_error_maps_to_422(self) -> None:
        def boom(*, collection: QuerySet[Post]) -> None:
            raise ServiceError("nope")

        class _View(ServiceDeleteView):
            spec = ServiceSpec(
                service=boom,
                collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts),
                atomic=False,
            )

        response = _View.as_view()(factory.delete("/"))
        assert response.status_code == 422

    def test_collection_selector_receives_url_kwargs(self) -> None:
        # Nested route ``/authors/{author_pk}/posts/`` — the collection selector
        # scopes to the parent from the URL, so the bulk delete only touches
        # that author's posts (regression: URL kwargs weren't reaching the
        # collection pool, unlike the instance/retrieve path).
        ada = Author.objects.create(name="Ada")
        grace = Author.objects.create(name="Grace")
        Post.objects.create(title="a1", author=ada)
        Post.objects.create(title="a2", author=ada)
        Post.objects.create(title="g1", author=grace)

        def _authors_posts(*, author_pk: int) -> QuerySet[Post]:
            return Post.objects.filter(author_id=author_pk)

        class _View(ServiceDeleteView):
            spec = ServiceSpec(
                service=delete_collection(Post),
                collection_selector_spec=SelectorSpec(
                    kind=SelectorKind.LIST, selector=_authors_posts
                ),
                atomic=False,
            )

        response = _View.as_view()(factory.delete("/"), author_pk=ada.pk)
        assert response.status_code == 204
        # Only Ada's posts were deleted; Grace's survive.
        assert set(Post.objects.values_list("title", flat=True)) == {"g1"}

    def test_url_kwargs_win_over_client_query_on_conflict(self) -> None:
        # A client can't override the route scope by passing the same key in the
        # query string — route captures are authoritative.
        ada = Author.objects.create(name="Ada")
        grace = Author.objects.create(name="Grace")
        Post.objects.create(title="a1", author=ada)
        Post.objects.create(title="g1", author=grace)

        def _by_author(*, author_pk: int) -> QuerySet[Post]:
            return Post.objects.filter(author_id=author_pk)

        class _View(ServiceDeleteView):
            spec = ServiceSpec(
                service=delete_collection(Post),
                collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_by_author),
                atomic=False,
            )

        # Client tries to redirect the scope to Grace via ?author_pk=<grace>.
        response = _View.as_view()(factory.delete(f"/?author_pk={grace.pk}"), author_pk=ada.pk)
        assert response.status_code == 204
        # The route's author_pk (Ada) won → Grace's post is untouched.
        assert set(Post.objects.values_list("title", flat=True)) == {"g1"}


@pytest.mark.django_db
class TestBulkUpdateCollection:
    def test_bulk_update_over_filter(self) -> None:
        Post.objects.create(title="a", published=False)
        Post.objects.create(title="b", published=False)

        def publish_all(*, collection: QuerySet[Post]) -> dict[str, int]:
            return {"updated": collection.update(published=True)}

        class _View(ServiceUpdateView):
            spec = ServiceSpec(
                service=publish_all,
                collection_selector_spec=SelectorSpec(
                    kind=SelectorKind.LIST, selector=_all_posts, filter_set=_PublishedFilterSet
                ),
                atomic=False,
            )

        response = _View.as_view()(factory.put("/?published=false"))
        assert response.status_code == 200
        assert response.data == {"updated": 2}
        assert Post.objects.filter(published=True).count() == 2

    def test_bulk_update_renders_list_output(self) -> None:
        Post.objects.create(title="a", published=False)
        Post.objects.create(title="b", published=False)
        Post.objects.create(title="c", published=True)

        def publish_drafts(*, collection: QuerySet[Post]) -> list[int]:
            ids = list(collection.values_list("id", flat=True))
            Post.objects.filter(id__in=ids).update(published=True)
            return ids

        def published_by_ids(*, result: list[int]) -> QuerySet[Post]:
            return Post.objects.filter(id__in=result).order_by("id")

        class _View(ServiceUpdateView):
            spec = ServiceSpec(
                service=publish_drafts,
                collection_selector_spec=SelectorSpec(
                    kind=SelectorKind.LIST, selector=_all_posts, filter_set=_PublishedFilterSet
                ),
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=published_by_ids,
                    output_serializer=_PostSerializer,
                ),
                atomic=False,
            )

        response = _View.as_view()(factory.put("/?published=false"))
        assert response.status_code == 200
        assert [row["title"] for row in response.data] == ["a", "b"]
        assert Post.objects.filter(published=True).count() == 3


class TestBulkValidation:
    def test_many_and_collection_mutually_exclusive(self) -> None:
        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_bulk_create,
                input_serializer=_PostIn,
                many=True,
                collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts),
            )

        with pytest.raises(ImproperlyConfigured, match="mutually exclusive"):
            _View.as_view()

    def test_collection_selector_must_be_list_kind(self) -> None:
        class _View(ServiceDeleteView):
            spec = ServiceSpec(
                service=delete_collection(Post),
                collection_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, selector=_all_posts
                ),
            )

        with pytest.raises(ImproperlyConfigured, match="must be SelectorKind.LIST"):
            _View.as_view()

    def test_collection_selector_requires_selector_at_as_view(self) -> None:
        class _View(ServiceDeleteView):
            spec = ServiceSpec(
                service=delete_collection(Post),
                collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST),
            )

        with pytest.raises(ImproperlyConfigured, match="requires a `selector`"):
            _View.as_view()

    def test_valid_collection_spec_passes(self) -> None:
        class _View(ServiceDeleteView):
            spec = ServiceSpec(
                service=delete_collection(Post),
                collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts),
            )

        assert _View.as_view() is not None
