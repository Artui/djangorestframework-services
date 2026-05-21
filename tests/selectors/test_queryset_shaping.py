"""Tests for SelectorSpec queryset shaping (select_related / prefetch_related /
annotations / extend_queryset).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Count, QuerySet
from django.db.models.query import Prefetch
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    SelectorKind,
    SelectorListView,
    SelectorRetrieveView,
    SelectorSpec,
    SelectorViewSet,
    selector_action,
)
from tests.testapp.models import Author, Post, Tag

factory = APIRequestFactory()


class _PostWithAuthorSerializer(serializers.ModelSerializer):
    """Touches the FK but not the M2M — for select_related-only tests."""

    author_name = serializers.CharField(source="author.name", allow_null=True)

    class Meta:
        model = Post
        fields = ("id", "title", "author_name")


class _PostWithTagsSerializer(serializers.ModelSerializer):
    """Touches both FK and M2M — for prefetch_related tests."""

    author_name = serializers.CharField(source="author.name", allow_null=True)
    tag_names = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = ("id", "title", "author_name", "tag_names")

    def get_tag_names(self, obj: Post) -> list[str]:
        return [t.name for t in obj.tags.all()]


def _all_posts() -> QuerySet[Post]:
    return Post.objects.all().order_by("id")


def _post_by_pk(*, pk: int) -> Post | None:
    return Post.objects.filter(pk=pk).first()


def _post_qs_by_pk(*, pk: int) -> QuerySet[Post]:
    """Retrieve selector that returns a QuerySet so the framework can shape it."""
    return Post.objects.filter(pk=pk)


@pytest.mark.django_db
class TestSelectRelated:
    def test_reduces_query_count_for_fk(self) -> None:
        author = Author.objects.create(name="Ada")
        Post.objects.create(title="a", author=author)
        Post.objects.create(title="b", author=author)

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_all_posts,
                output_serializer=_PostWithAuthorSerializer,
                select_related=["author"],
            )

        with django_assert_num_queries(1):
            response = _View.as_view()(factory.get("/"))
        assert response.status_code == 200
        assert all(item["author_name"] == "Ada" for item in response.data)


@pytest.mark.django_db
class TestPrefetchRelated:
    def test_prefetch_relation_by_name(self) -> None:
        author = Author.objects.create(name="Ada")
        tag = Tag.objects.create(name="t1")
        post = Post.objects.create(title="p", author=author)
        post.tags.add(tag)

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_all_posts,
                output_serializer=_PostWithTagsSerializer,
                select_related=["author"],
                prefetch_related=["tags"],
            )

        with django_assert_num_queries(2):  # one for posts+author, one for tags
            response = _View.as_view()(factory.get("/"))
        assert response.data[0]["tag_names"] == ["t1"]

    def test_prefetch_object_accepted(self) -> None:
        author = Author.objects.create(name="Ada")
        tag = Tag.objects.create(name="t1")
        post = Post.objects.create(title="p", author=author)
        post.tags.add(tag)

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_all_posts,
                output_serializer=_PostWithTagsSerializer,
                select_related=["author"],
                prefetch_related=[Prefetch("tags", queryset=Tag.objects.order_by("name"))],
            )

        response = _View.as_view()(factory.get("/"))
        assert response.data[0]["tag_names"] == ["t1"]


@pytest.mark.django_db
class TestAnnotations:
    def test_annotation_appears_on_objects(self) -> None:
        author = Author.objects.create(name="Ada")
        Post.objects.create(title="p1", author=author)
        Post.objects.create(title="p2", author=author)

        def _authors() -> QuerySet[Author]:
            return Author.objects.all().order_by("id")

        class _AuthorWithCountSerializer(serializers.ModelSerializer):
            post_count = serializers.IntegerField(read_only=True)

            class Meta:
                model = Author
                fields = ("id", "name", "post_count")

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_authors,
                output_serializer=_AuthorWithCountSerializer,
                annotations={"post_count": Count("posts")},
            )

        response = _View.as_view()(factory.get("/"))
        assert response.data[0]["post_count"] == 2


@pytest.mark.django_db
class TestExtendQueryset:
    def test_callable_invoked_with_qs_view_request(self) -> None:
        author = Author.objects.create(name="Ada")
        Post.objects.create(title="p", author=author)
        captured: dict[str, Any] = {}

        def extend(qs: QuerySet[Post], view: Any, request: Any) -> QuerySet[Post]:
            captured["qs_type"] = type(qs).__name__
            captured["view"] = view
            captured["request"] = request
            return qs.select_related("author")

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_all_posts,
                output_serializer=_PostWithAuthorSerializer,
                extend_queryset=extend,
            )

        with django_assert_num_queries(1):
            _View.as_view()(factory.get("/"))
        assert captured["qs_type"] == "QuerySet"
        assert isinstance(captured["view"], _View)
        assert captured["request"] is not None

    def test_dynamic_shaping_branches_on_query_param(self) -> None:
        """The case the user library actually has: include relations only
        when the request opts in."""
        author = Author.objects.create(name="Ada")
        tag = Tag.objects.create(name="t1")
        post = Post.objects.create(title="p", author=author)
        post.tags.add(tag)

        def extend(qs: QuerySet[Post], view: Any, request: Any) -> QuerySet[Post]:
            if "tags" in request.query_params.get("include", "").split(","):
                return qs.prefetch_related("tags")
            return qs

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_all_posts,
                output_serializer=_PostWithTagsSerializer,
                select_related=["author"],
                extend_queryset=extend,
            )

        # Without ?include=tags — prefetch is skipped, so accessing tags
        # would normally trigger an extra query per row.
        with django_assert_num_queries(2):  # post+author, then tags lazily
            _View.as_view()(factory.get("/"))

        # With ?include=tags — prefetch runs.
        with django_assert_num_queries(2):  # post+author, prefetched tags
            _View.as_view()(factory.get("/?include=tags"))

    def test_runs_after_declarative_shaping(self) -> None:
        """extend_queryset sees the declaratively-shaped queryset."""
        author = Author.objects.create(name="Ada")
        Post.objects.create(title="p", author=author)
        observed_query_str: dict[str, str] = {}

        def extend(qs: QuerySet[Post], view: Any, request: Any) -> QuerySet[Post]:
            observed_query_str["sql"] = str(qs.query)
            return qs

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_all_posts,
                output_serializer=_PostWithAuthorSerializer,
                select_related=["author"],
                extend_queryset=extend,
            )

        _View.as_view()(factory.get("/"))
        # The select_related join is part of the SQL the callable sees.
        assert "JOIN" in observed_query_str["sql"].upper()


@pytest.mark.django_db
class TestRetrieve:
    def test_select_related_applied_on_retrieve(self) -> None:
        author = Author.objects.create(name="Ada")
        post = Post.objects.create(title="p", author=author)

        class _View(SelectorRetrieveView):
            spec = SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_post_qs_by_pk,
                output_serializer=_PostWithAuthorSerializer,
                select_related=["author"],
            )

        with django_assert_num_queries(1):
            response = _View.as_view()(factory.get("/"), pk=post.pk)
        assert response.data["author_name"] == "Ada"


@pytest.mark.django_db
class TestViewsetMixins:
    def test_select_related_on_list_action(self) -> None:
        author = Author.objects.create(name="Ada")
        Post.objects.create(title="p", author=author)

        class _ViewSet(SelectorViewSet):
            queryset = Post.objects.all()
            action_specs = {
                "list": SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_all_posts,
                    output_serializer=_PostWithAuthorSerializer,
                    select_related=["author"],
                ),
            }

        with django_assert_num_queries(1):
            response = _ViewSet.as_view({"get": "list"})(factory.get("/"))
        assert response.data[0]["author_name"] == "Ada"

    def test_extend_queryset_on_retrieve_action(self) -> None:
        author = Author.objects.create(name="Ada")
        post = Post.objects.create(title="p", author=author)

        def extend(qs: QuerySet[Post], view: Any, request: Any) -> QuerySet[Post]:
            return qs.select_related("author")

        class _ViewSet(SelectorViewSet):
            queryset = Post.objects.all()
            action_specs = {
                "retrieve": SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_post_qs_by_pk,
                    output_serializer=_PostWithAuthorSerializer,
                    extend_queryset=extend,
                ),
            }

        with django_assert_num_queries(1):
            response = _ViewSet.as_view({"get": "retrieve"})(factory.get("/"), pk=post.pk)
        assert response.data["author_name"] == "Ada"

    def test_shaping_on_selector_action_decorator(self) -> None:
        from rest_framework.viewsets import GenericViewSet

        author = Author.objects.create(name="Ada")
        Post.objects.create(title="p", author=author)

        class _ViewSet(GenericViewSet):
            queryset = Post.objects.all()
            serializer_class = _PostWithAuthorSerializer

            @selector_action(
                SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_all_posts,
                    output_serializer=_PostWithAuthorSerializer,
                    select_related=["author"],
                ),
                detail=False,
            )
            def latest(self, request):  # type: ignore[no-untyped-def]
                """Stubbed."""

        with django_assert_num_queries(1):
            response = _ViewSet.as_view({"get": "latest"})(factory.get("/"))
        assert response.data[0]["author_name"] == "Ada"


@pytest.mark.django_db
class TestNonQuerySetReturn:
    def test_raises_when_selector_returns_list_with_shaping(self) -> None:
        Author.objects.create(name="Ada")

        def _list_of_dicts() -> list[dict[str, Any]]:
            return [{"id": 1, "title": "x", "author_name": None, "tag_names": []}]

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_list_of_dicts,
                output_serializer=_PostWithAuthorSerializer,
                select_related=["author"],
            )

        with pytest.raises(ImproperlyConfigured, match="not a Django QuerySet"):
            _View.as_view()(factory.get("/"))


class TestValidation:
    def test_shaping_without_selector_raises_at_as_view(self) -> None:
        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                output_serializer=_PostWithAuthorSerializer,
                select_related=["author"],
            )

        with pytest.raises(ImproperlyConfigured, match="selector"):
            _View.as_view()

    def test_extend_queryset_without_selector_raises_at_as_view(self) -> None:
        def extend(qs: Any, view: Any, request: Any) -> Any:
            return qs

        class _View(SelectorListView):
            spec = SelectorSpec(
                kind=SelectorKind.LIST,
                output_serializer=_PostWithAuthorSerializer,
                extend_queryset=extend,
            )

        with pytest.raises(ImproperlyConfigured, match="selector"):
            _View.as_view()


# -------- ServiceSpec shaping (applied to output_selector) ----------------


from dataclasses import dataclass  # noqa: E402

from rest_framework_services import ServiceCreateView, ServiceSpec  # noqa: E402


@dataclass
class _PostIn:
    title: str
    author_id: int


def _create_post(*, data: _PostIn) -> Post:
    return Post.objects.create(title=data.title, author_id=data.author_id)


def _refetch_post_qs(*, result: Post) -> QuerySet[Post]:
    """Output selector that returns a QuerySet for the framework to shape."""
    return Post.objects.filter(pk=result.pk)


@pytest.mark.django_db
class TestServiceSpecShaping:
    def test_select_related_applied_to_output_selector(self) -> None:
        author = Author.objects.create(name="Ada")

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create_post,
                input_serializer=_PostIn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_refetch_post_qs,
                    output_serializer=_PostWithAuthorSerializer,
                    select_related=["author"],
                ),
                atomic=False,
            )

        # 2 queries: one INSERT for the post, one SELECT (post+author joined).
        with django_assert_num_queries(2):
            response = _View.as_view()(
                factory.post(
                    "/",
                    {"title": "p", "author_id": author.pk},
                    format="json",
                ),
            )
        assert response.status_code == 201
        assert response.data["author_name"] == "Ada"

    def test_extend_queryset_runs_on_output_selector(self) -> None:
        author = Author.objects.create(name="Ada")
        captured: dict[str, Any] = {}

        def extend(qs: QuerySet[Post], view: Any, request: Any) -> QuerySet[Post]:
            captured["called"] = True
            return qs.select_related("author")

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create_post,
                input_serializer=_PostIn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_refetch_post_qs,
                    output_serializer=_PostWithAuthorSerializer,
                    extend_queryset=extend,
                ),
                atomic=False,
            )

        response = _View.as_view()(
            factory.post(
                "/",
                {"title": "p", "author_id": author.pk},
                format="json",
            ),
        )
        assert captured["called"] is True
        assert response.data["author_name"] == "Ada"

    def test_instance_returning_output_selector_passes_through_without_shaping(
        self,
    ) -> None:
        """Backward-compat: output_selector returning an instance still works
        when no shaping is configured."""
        author = Author.objects.create(name="Ada")

        def _refetch_instance(*, result: Post) -> Post | None:
            return Post.objects.filter(pk=result.pk).first()

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create_post,
                input_serializer=_PostIn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_refetch_instance,
                    output_serializer=_PostWithAuthorSerializer,
                ),
                atomic=False,
            )

        response = _View.as_view()(
            factory.post(
                "/",
                {"title": "p", "author_id": author.pk},
                format="json",
            ),
        )
        assert response.status_code == 201
        assert response.data["title"] == "p"

    def test_non_queryset_return_with_shaping_raises(self) -> None:
        """Shaping configured but output_selector returned an instance."""
        author = Author.objects.create(name="Ada")

        def _refetch_instance(*, result: Post) -> Post | None:
            return Post.objects.filter(pk=result.pk).first()

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create_post,
                input_serializer=_PostIn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_refetch_instance,
                    output_serializer=_PostWithAuthorSerializer,
                    select_related=["author"],
                ),
                atomic=False,
            )

        with pytest.raises(ImproperlyConfigured, match="not a Django QuerySet"):
            _View.as_view()(
                factory.post(
                    "/",
                    {"title": "p", "author_id": author.pk},
                    format="json",
                ),
            )

    def test_works_with_viewset_create_action(self) -> None:
        from rest_framework_services import ServiceViewSet

        author = Author.objects.create(name="Ada")

        class _ViewSet(ServiceViewSet):
            queryset = Post.objects.all()
            action_specs = {
                "create": ServiceSpec(
                    service=_create_post,
                    input_serializer=_PostIn,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE,
                        selector=_refetch_post_qs,
                        output_serializer=_PostWithAuthorSerializer,
                        select_related=["author"],
                    ),
                    atomic=False,
                ),
            }

        response = _ViewSet.as_view({"post": "create"})(
            factory.post("/", {"title": "p", "author_id": author.pk}, format="json"),
        )
        assert response.status_code == 201
        assert response.data["author_name"] == "Ada"


class TestServiceSpecShapingValidation:
    def test_shaping_without_nested_selector_raises_at_as_view(self) -> None:
        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create_post,
                input_serializer=_PostIn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    output_serializer=_PostWithAuthorSerializer,
                    select_related=["author"],
                ),
            )

        with pytest.raises(ImproperlyConfigured, match="selector"):
            _View.as_view()

    def test_extend_queryset_without_nested_selector_raises(self) -> None:
        def extend(qs: Any, view: Any, request: Any) -> Any:
            return qs

        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_create_post,
                input_serializer=_PostIn,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    output_serializer=_PostWithAuthorSerializer,
                    extend_queryset=extend,
                ),
            )

        with pytest.raises(ImproperlyConfigured, match="selector"):
            _View.as_view()


# -------- pytest-django helper -------------------------------------------

# We use ``CaptureQueriesContext`` directly to avoid pytest-django fixture
# plumbing in xunit-style classes.


from django.db import DEFAULT_DB_ALIAS, connections  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402


class _AssertNumQueriesContext(CaptureQueriesContext):
    def __init__(self, expected: int) -> None:
        super().__init__(connections[DEFAULT_DB_ALIAS])
        self._expected = expected

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        super().__exit__(exc_type, exc_value, traceback)
        if exc_type is not None:
            return
        executed = len(self.captured_queries)
        if executed != self._expected:
            queries = "\n".join(q["sql"] for q in self.captured_queries)
            raise AssertionError(f"Expected {self._expected} queries, got {executed}:\n{queries}")


def django_assert_num_queries(n: int) -> _AssertNumQueriesContext:
    return _AssertNumQueriesContext(n)
