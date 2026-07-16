"""Transport-neutral ``dispatch_spec`` + ``render_spec_output`` (sync)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from rest_framework import serializers

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceError,
    ServiceSpec,
    dispatch_spec,
    render_spec_output,
)
from tests.testapp.models import Author, Post


@dataclass
class _PostIn:
    title: str


class _PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ("id", "title", "published")


class _PublishedFilterSet:
    """Duck-typed FilterSet: narrows by ``?published=``."""

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


def _post_qs_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


def _create_post(*, data: _PostIn) -> Post:
    return Post.objects.create(title=data.title)


def _update_post(*, instance: Post, data: _PostIn) -> Post:
    instance.title = data.title
    instance.save(update_fields=["title"])
    return instance


@pytest.mark.django_db
class TestDispatchSelector:
    def test_list_returns_shaped_filtered_queryset(self) -> None:
        author = Author.objects.create(name="A")
        Post.objects.create(title="shipped", author=author, published=True)
        Post.objects.create(title="draft", author=author, published=False)
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_all_posts,
            output_serializer=_PostSerializer,
            select_related=["author"],
            filter_set=_PublishedFilterSet,
        )
        result = dispatch_spec(spec, user=None, params={"published": "true"})
        assert result.kind == "list"
        assert result.status == 200
        rendered = render_spec_output(spec, result.value, many=True)
        assert [row["title"] for row in rendered] == ["shipped"]

    def test_retrieve_found(self) -> None:
        post = Post.objects.create(title="p")
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk)
        result = dispatch_spec(spec, user=None, params={"pk": post.pk})
        assert result.kind == "instance"
        assert result.value == post

    def test_retrieve_missing_is_not_found(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk)
        result = dispatch_spec(spec, user=None, params={"pk": 9999})
        assert result == DispatchResult(value=None, kind="not_found", status=404)

    def test_retrieve_allow_none_renders_null(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk, allow_none=True)
        result = dispatch_spec(spec, user=None, params={"pk": 9999})
        assert result == DispatchResult(value=None, kind="instance", status=200)

    def test_object_does_not_exist_on_retrieve_is_not_found(self) -> None:
        def strict_get(*, pk: int) -> Post:
            return Post.objects.get(pk=pk)

        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=strict_get)
        result = dispatch_spec(spec, user=None, params={"pk": 9999})
        assert result.kind == "not_found"

    def test_object_does_not_exist_on_list_reraises(self) -> None:
        def strict_get(*, pk: int) -> Post:
            return Post.objects.get(pk=pk)

        spec = SelectorSpec(kind=SelectorKind.LIST, selector=strict_get)
        with pytest.raises(Post.DoesNotExist):
            dispatch_spec(spec, user=None, params={"pk": 9999})

    def test_selector_required(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST)
        with pytest.raises(ImproperlyConfigured, match="requires the SelectorSpec"):
            dispatch_spec(spec, user=None, params={})

    def test_spec_kwargs_provider_feeds_the_selector(self) -> None:
        Post.objects.create(title="a")
        seen: dict[str, Any] = {}

        def only_titled(*, wanted: str) -> QuerySet[Post]:
            seen["wanted"] = wanted
            return Post.objects.filter(title=wanted)

        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=only_titled,
            kwargs=lambda: {"wanted": "a"},
        )
        result = dispatch_spec(spec, user=None, params={})
        assert seen["wanted"] == "a"
        assert list(result.value) == list(Post.objects.filter(title="a"))


@pytest.mark.django_db
class TestDispatchService:
    def test_create_no_instance(self) -> None:
        spec = ServiceSpec(
            service=_create_post, input_serializer=_PostIn, success_status=201, atomic=False
        )
        result = dispatch_spec(spec, user=None, params={"title": "new"})
        assert result.kind == "instance"
        assert result.status == 201
        assert result.value.title == "new"

    def test_update_resolves_instance_from_params(self) -> None:
        post = Post.objects.create(title="old")
        spec = ServiceSpec(
            service=_update_post,
            input_serializer=_PostIn,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk
            ),
            atomic=False,
        )
        result = dispatch_spec(spec, user=None, params={"pk": post.pk, "title": "fresh"})
        assert result.status == 200
        post.refresh_from_db()
        assert post.title == "fresh"

    def test_missing_instance_is_not_found(self) -> None:
        spec = ServiceSpec(
            service=_update_post,
            input_serializer=_PostIn,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk
            ),
        )
        result = dispatch_spec(spec, user=None, params={"pk": 9999, "title": "x"})
        assert result == DispatchResult(value=None, kind="not_found", status=404)

    def test_instance_selector_object_does_not_exist_is_not_found(self) -> None:
        def strict_get(*, pk: int) -> Post:
            return Post.objects.get(pk=pk)

        spec = ServiceSpec(
            service=_update_post,
            input_serializer=_PostIn,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=strict_get),
        )
        result = dispatch_spec(spec, user=None, params={"pk": 9999, "title": "x"})
        assert result.kind == "not_found"

    def test_output_selector_refetches_and_renders(self) -> None:
        def refetch(*, result: Post) -> QuerySet[Post]:
            return Post.objects.filter(pk=result.pk)

        spec = ServiceSpec(
            service=_create_post,
            input_serializer=_PostIn,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=refetch,
                output_serializer=_PostSerializer,
            ),
            success_status=201,
            atomic=False,
        )
        result = dispatch_spec(spec, user=None, params={"title": "p"})
        rendered = render_spec_output(spec, result.value, extras={"result": result.value})
        assert rendered["title"] == "p"

    def test_success_status_override(self) -> None:
        spec = ServiceSpec(service=_create_post, input_serializer=_PostIn, atomic=False)
        result = dispatch_spec(spec, user=None, params={"title": "p"}, success_status=202)
        assert result.status == 202

    def test_default_status_is_200(self) -> None:
        spec = ServiceSpec(service=_create_post, input_serializer=_PostIn, atomic=False)
        result = dispatch_spec(spec, user=None, params={"title": "p"})
        assert result.status == 200

    def test_callable_success_status_keys_on_service_result(self) -> None:
        def status(*, result: Post) -> int:
            return 201 if result.title == "new" else 200

        spec = ServiceSpec(
            service=_create_post,
            input_serializer=_PostIn,
            success_status=status,
            atomic=False,
        )
        assert dispatch_spec(spec, user=None, params={"title": "new"}).status == 201
        assert dispatch_spec(spec, user=None, params={"title": "old"}).status == 200

    def test_callable_success_status_sees_instance_on_update(self) -> None:
        post = Post.objects.create(title="old")

        def status(*, instance: Post) -> int:
            return 209 if instance.pk == post.pk else 200

        spec = ServiceSpec(
            service=_update_post,
            input_serializer=_PostIn,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk
            ),
            success_status=status,
            atomic=False,
        )
        result = dispatch_spec(spec, user=None, params={"pk": post.pk, "title": "fresh"})
        assert result.status == 209

    def test_callable_success_status_override_wins(self) -> None:
        def status(**_: Any) -> int:
            return 200

        spec = ServiceSpec(
            service=_create_post, input_serializer=_PostIn, success_status=status, atomic=False
        )
        # An explicit override short-circuits the callable entirely.
        result = dispatch_spec(spec, user=None, params={"title": "p"}, success_status=299)
        assert result.status == 299

    def test_service_no_input_serializer(self) -> None:
        captured: dict[str, Any] = {}

        def touch(*, user: Any) -> Post:
            captured["user"] = user
            return Post.objects.create(title="t")

        spec = ServiceSpec(service=touch, atomic=False)
        result = dispatch_spec(spec, user="bob", params={})
        assert captured["user"] == "bob"
        assert result.value.title == "t"

    def test_service_error_propagates(self) -> None:
        def boom(*, data: _PostIn) -> None:
            raise ServiceError("nope")

        spec = ServiceSpec(service=boom, input_serializer=_PostIn, atomic=False)
        with pytest.raises(ServiceError, match="nope"):
            dispatch_spec(spec, user=None, params={"title": "x"})

    def test_input_serializer_context_is_resolved(self) -> None:
        seen: dict[str, Any] = {}

        class _CtxSerializer(serializers.Serializer):
            title = serializers.CharField()

            def validate(self, attrs: Any) -> Any:
                seen["flag"] = self.context.get("flag")
                return attrs

        def svc(*, data: Any) -> Post:
            return Post.objects.create(title=data["title"])

        spec = ServiceSpec(
            service=svc,
            input_serializer=_CtxSerializer,
            input_serializer_context=lambda: {"flag": "on"},
            atomic=False,
        )
        dispatch_spec(spec, user=None, params={"title": "p"})
        assert seen["flag"] == "on"


class TestDispatchTypeError:
    def test_rejects_non_spec(self) -> None:
        with pytest.raises(TypeError, match="ServiceSpec or SelectorSpec"):
            dispatch_spec("nope", user=None, params={})  # type: ignore[arg-type]


@pytest.mark.django_db
class TestRenderSpecOutput:
    def test_passthrough_collection_without_serializer(self) -> None:
        Post.objects.create(title="a")
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts)
        result = dispatch_spec(spec, user=None, params={})
        rendered = render_spec_output(spec, result.value, many=True)
        assert isinstance(rendered, list)
        assert len(rendered) == 1

    def test_passthrough_scalar_when_not_iterable(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: 7)
        result = dispatch_spec(spec, user=None, params={})
        assert render_spec_output(spec, result.value, many=True) == 7

    def test_passthrough_single_without_serializer(self) -> None:
        post = Post.objects.create(title="a")
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_qs_by_pk)
        result = dispatch_spec(spec, user=None, params={"pk": post.pk})
        assert render_spec_output(spec, result.value) is result.value

    def test_output_context_provider_applied(self) -> None:
        Post.objects.create(title="a")
        Post.objects.create(title="b")

        class _CountingSerializer(serializers.ModelSerializer):
            n = serializers.SerializerMethodField()

            class Meta:
                model = Post
                fields = ("id", "n")

            def get_n(self, obj: Post) -> int:
                return self.context.get("n", -1)

        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_all_posts,
            output_serializer=_CountingSerializer,
            output_serializer_context=lambda *, page: {"n": len(list(page))},
        )
        result = dispatch_spec(spec, user=None, params={})
        page = list(result.value)
        rendered = render_spec_output(spec, page, many=True, extras={"page": page})
        assert all(row["n"] == 2 for row in rendered)
