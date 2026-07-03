"""Tests for ServiceUpdateView."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import SelectorKind, SelectorSpec, ServiceSpec, ServiceUpdateView
from tests.testapp.models import Author, Post
from tests.testapp.serializers import AuthorSerializer


@dataclass
class _UpdateAuthorInput:
    name: str


def _update_author(*, instance: Author, data: _UpdateAuthorInput) -> Author:
    instance.name = data.name
    instance.save(update_fields=["name"])
    return instance


class _UpdateAuthorView(ServiceUpdateView):
    queryset = Author.objects.all()
    spec = ServiceSpec(
        service=_update_author,
        input_serializer=_UpdateAuthorInput,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
        ),
    )


factory = APIRequestFactory()


@pytest.mark.django_db
class TestServiceUpdateView:
    def test_put_full_update(self) -> None:
        author = Author.objects.create(name="orig")
        request = factory.put("/", {"name": "new"}, format="json")
        response = _UpdateAuthorView.as_view()(request, pk=author.pk)
        author.refresh_from_db()
        assert response.status_code == 200
        assert author.name == "new"

    def test_patch_partial_update(self) -> None:
        author = Author.objects.create(name="orig")
        request = factory.patch("/", {"name": "new"}, format="json")
        response = _UpdateAuthorView.as_view()(request, pk=author.pk)
        author.refresh_from_db()
        assert response.status_code == 200
        assert author.name == "new"

    def test_missing_spec_raises(self) -> None:
        class _Empty(ServiceUpdateView):
            queryset = Author.objects.all()

        author = Author.objects.create(name="x")
        request = factory.put("/", {"name": "y"}, format="json")
        with pytest.raises(NotImplementedError):
            _Empty.as_view()(request, pk=author.pk)

    def test_404_when_instance_missing(self) -> None:
        request = factory.put("/", {"name": "x"}, format="json")
        response = _UpdateAuthorView.as_view()(request, pk=999)
        assert response.status_code == 404

    def test_get_object_overridable(self) -> None:
        author = Author.objects.create(name="orig")

        class _Custom(ServiceUpdateView):
            spec = ServiceSpec(
                service=_update_author,
                input_serializer=_UpdateAuthorInput,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
            )

            def get_object(self) -> Any:
                return author

        request = factory.put("/", {"name": "z"}, format="json")
        response = _Custom.as_view()(request)
        author.refresh_from_db()
        assert response.status_code == 200
        assert author.name == "z"

    def test_output_selector(self) -> None:
        author = Author.objects.create(name="orig")

        def selector(*, instance: Author) -> dict[str, Any]:
            return {"latest_name": instance.name}

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=_update_author,
                input_serializer=_UpdateAuthorInput,
                output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector),
            )

        request = factory.patch("/", {"name": "fresh"}, format="json")
        response = _View.as_view()(request, pk=author.pk)
        assert response.data == {"latest_name": "fresh"}

    def test_service_error_maps_to_422(self) -> None:
        from rest_framework_services import ServiceError

        def boom(*, instance: Author, data: _UpdateAuthorInput) -> None:
            raise ServiceError("nope")

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            spec = ServiceSpec(service=boom, input_serializer=_UpdateAuthorInput, atomic=False)

        author = Author.objects.create(name="x")
        request = factory.patch("/", {"name": "y"}, format="json")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 422

    def test_no_input_serializer(self) -> None:
        captured: dict[str, Any] = {}

        def fn(*, instance: Author) -> Author:
            captured["pk"] = instance.pk
            return instance

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=fn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
            )

        author = Author.objects.create(name="hi")
        request = factory.put("/", {}, format="json")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 200
        assert captured["pk"] == author.pk

    def test_output_selector_returning_none_renders_204(self) -> None:
        def fn(*, instance: Author, data: _UpdateAuthorInput) -> Author:
            return instance

        def selector(*, result: Author) -> None:
            return None

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=fn,
                input_serializer=_UpdateAuthorInput,
                output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector),
            )

        author = Author.objects.create(name="x")
        request = factory.patch("/", {"name": "y"}, format="json")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 204

    def test_falls_back_to_instance_when_service_returns_none(self) -> None:
        """When output_selector is missing AND service returns None, render
        the in-memory instance (which the service mutated in place)."""
        author = Author.objects.create(name="orig")

        def void_update(*, instance: Author, data: _UpdateAuthorInput) -> None:
            instance.name = data.name
            instance.save(update_fields=["name"])
            # Returns None on purpose.

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=void_update,
                input_serializer=_UpdateAuthorInput,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
                ),
            )

        request = factory.patch("/", {"name": "renamed"}, format="json")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 200
        assert response.data["name"] == "renamed"

    def test_no_output_serializer_with_none_service_renders_empty_body(self) -> None:
        """A no-output update whose service returns ``None`` renders an empty
        204 body rather than trying (and failing) to serialize the raw
        in-memory instance."""
        author = Author.objects.create(name="orig")

        def void_update(*, instance: Author, data: _UpdateAuthorInput) -> None:
            instance.name = data.name
            instance.save(update_fields=["name"])

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            spec = ServiceSpec(service=void_update, input_serializer=_UpdateAuthorInput)

        request = factory.patch("/", {"name": "renamed"}, format="json")
        response = _View.as_view()(request, pk=author.pk)
        assert response.status_code == 204
        assert response.data is None
        assert response.render().content == b""
        author.refresh_from_db()
        assert author.name == "renamed"

    def test_no_input_no_output_update(self) -> None:
        """An update with neither an input serializer nor an output serializer
        is supported: the service runs on the looked-up instance and the
        response is an empty 204."""
        author = Author.objects.create(name="orig")

        def touch(*, instance: Author) -> None:
            instance.name = "touched"
            instance.save(update_fields=["name"])

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            spec = ServiceSpec(service=touch)

        response = _View.as_view()(factory.patch("/"), pk=author.pk)
        assert response.status_code == 204
        assert response.data is None
        author.refresh_from_db()
        assert author.name == "touched"


@pytest.mark.django_db
class TestPrefetchCacheCleared:
    """A mutating service that changes a prefetched relation renders
    fresh data, mirroring DRF's ``UpdateModelMixin`` prefetch-cache invalidation.

    The target is resolved with its ``posts`` reverse FK prefetched, then the
    service adds a row via the ``Post`` manager — a path Django does *not*
    self-invalidate (unlike an m2m ``.add()``), so the parent's prefetch cache
    stays stale unless the framework clears it.
    """

    def test_renders_fresh_related_data_after_service_mutates_relation(self) -> None:
        author = Author.objects.create(name="Ada")
        Post.objects.create(title="old", author=author)

        def fetch_author(*, pk: int) -> Any:
            return Author.objects.filter(pk=pk)

        def add_post(*, instance: Author) -> None:
            # Creates a related row without touching ``instance.posts``, so the
            # prefetch cache is left stale; returns None so the update flow
            # re-serializes the in-memory instance.
            Post.objects.create(title="new", author=instance)

        class _AuthorPostsSerializer(serializers.ModelSerializer):
            post_titles = serializers.SerializerMethodField()

            class Meta:
                model = Author
                fields = ("id", "post_titles")

            def get_post_titles(self, obj: Author) -> list[str]:
                return sorted(p.title for p in obj.posts.all())

        class _View(ServiceUpdateView):
            queryset = Author.objects.all()
            spec = ServiceSpec(
                service=add_post,
                # Resolves the target with ``posts`` prefetched, so its cache is
                # populated (and goes stale) before the service mutates it.
                instance_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=fetch_author,
                    prefetch_related=["posts"],
                ),
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=_AuthorPostsSerializer
                ),
                atomic=False,
            )

        response = _View.as_view()(factory.put("/", {}, format="json"), pk=author.pk)
        assert response.status_code == 200
        # Without the prefetch-cache clear this would be the stale ["old"].
        assert response.data["post_titles"] == ["new", "old"]


@pytest.mark.django_db
class TestOutputSelectorFilterSetParity:
    """The HTTP mutation output re-fetch applies
    ``output_selector_spec.filter_set`` (filter_data falls back to
    ``request.query_params``), matching ``dispatch_spec``'s output re-fetch.
    Previously the HTTP path silently ignored the nested filter_set.
    """

    @staticmethod
    def _build_view(seen: list[Any]) -> type[ServiceUpdateView]:
        class _PublishedFilterSet:
            def __init__(self, *, data: Any, queryset: Any) -> None:
                # ``data`` is ``request.query_params`` (a QueryDict) on the HTTP
                # path; record the scalar the filter reads to prove parity.
                seen.append(data.get("published"))
                self._data = data
                self._queryset = queryset

            @property
            def qs(self) -> Any:
                raw = self._data.get("published")
                if raw is None:
                    return self._queryset
                keep = str(raw).lower() in ("1", "true", "yes")
                return self._queryset.filter(published=keep)

        class _TitleIn(serializers.Serializer):
            title = serializers.CharField()

        class _PostSerializer(serializers.ModelSerializer):
            class Meta:
                model = Post
                fields = ("id", "title", "published")

        def _publish(*, instance: Post, data: dict[str, Any]) -> Post:
            instance.title = data["title"]
            instance.published = True
            instance.save(update_fields=["title", "published"])
            return instance

        def _refetch(*, result: Post) -> Any:
            return Post.objects.filter(pk=result.pk)

        class _View(ServiceUpdateView):
            queryset = Post.objects.all()
            spec = ServiceSpec(
                service=_publish,
                input_serializer=_TitleIn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_refetch,
                    filter_set=_PublishedFilterSet,
                    output_serializer=_PostSerializer,
                ),
                atomic=False,
            )

        return _View

    def test_filter_set_keeps_matching_row(self) -> None:
        post = Post.objects.create(title="orig", published=False)
        seen: list[Any] = []
        view = self._build_view(seen)
        request = factory.patch("/?published=true", {"title": "updated"}, format="json")
        response = view.as_view()(request, pk=post.pk)
        assert response.status_code == 200
        assert response.data["title"] == "updated"
        # Proof of parity: the filter_set ran with the query params as filter_data.
        assert seen == ["true"]

    def test_filter_set_excludes_nonmatching_row(self) -> None:
        post = Post.objects.create(title="orig", published=False)
        seen: list[Any] = []
        view = self._build_view(seen)
        request = factory.patch("/?published=false", {"title": "updated"}, format="json")
        response = view.as_view()(request, pk=post.pk)
        # The service still published the row, but the output filter_set now
        # excludes it (published=True doesn't match ?published=false), so the
        # RETRIEVE re-fetch collapses to None. Pre-fix the HTTP path ignored the
        # filter_set and returned the row.
        post.refresh_from_db()
        assert post.published is True
        assert response.data.get("id") is None
        assert seen == ["false"]
