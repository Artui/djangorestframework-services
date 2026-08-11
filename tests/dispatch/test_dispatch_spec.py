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
    build_offline_context,
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

    def test_forwards_request_to_request_scoped_filter_set(self) -> None:
        """An off-HTTP caller that builds an offline context (as the MCP /
        Pydantic-AI bridges do) has its synthetic request forwarded into the
        FilterSet, so a request-scoped filter sees the acting user (``.user``)
        rather than ``None`` — the seam works transport-neutrally, not only on
        HTTP."""
        captured: dict[str, Any] = {}

        class _RequestAwareFilterSet:
            def __init__(self, *, data: Any, queryset: QuerySet[Post], request: Any = None) -> None:
                captured["request"] = request
                self._queryset = queryset

            @property
            def qs(self) -> QuerySet[Post]:
                return self._queryset

        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=_all_posts, filter_set=_RequestAwareFilterSet
        )
        context = build_offline_context("ada", {})
        dispatch_spec(spec, user="ada", params={}, request=context.request, view=context.view)

        assert captured["request"] is context.request
        assert captured["request"].user == "ada"

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

    def test_baseline_context_supplies_request_without_a_provider(self) -> None:
        """The reported regression: a serializer reading ``context["request"]``.

        Over HTTP ``get_serializer_context()`` always supplies it, so serializers
        read it unguarded; off HTTP the render used to pass no context at all and
        the same serializer raised ``KeyError: 'request'``.
        """
        Post.objects.create(title="a")

        class _RequestReadingSerializer(serializers.ModelSerializer):
            is_editable = serializers.SerializerMethodField()

            class Meta:
                model = Post
                fields = ("id", "is_editable")

            def get_is_editable(self, obj: Post) -> str:
                return str(self.context["request"].user)

        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=_all_posts, output_serializer=_RequestReadingSerializer
        )
        offline = build_offline_context(user="alice")
        result = dispatch_spec(
            spec, user="alice", params={}, request=offline.request, view=offline.view
        )
        rendered = render_spec_output(
            spec, result.value, many=True, request=offline.request, view=offline.view
        )
        assert [row["is_editable"] for row in rendered] == ["alice"]

    def test_baseline_context_carries_view_and_format(self) -> None:
        Post.objects.create(title="a")
        seen: dict[str, Any] = {}

        class _ContextEchoSerializer(serializers.ModelSerializer):
            echo = serializers.SerializerMethodField()

            class Meta:
                model = Post
                fields = ("id", "echo")

            def get_echo(self, obj: Post) -> str:
                seen.update(self.context)
                return obj.title

        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_post_qs_by_pk,
            output_serializer=_ContextEchoSerializer,
        )
        offline = build_offline_context(user="alice", kwargs={"pk": Post.objects.first().pk})
        result = dispatch_spec(
            spec,
            user="alice",
            params={"pk": Post.objects.first().pk},
            request=offline.request,
            view=offline.view,
        )
        render_spec_output(spec, result.value, request=offline.request, view=offline.view)
        assert seen["view"] is offline.view
        assert seen["format"] is None

    def test_output_context_provider_overrides_the_baseline(self) -> None:
        Post.objects.create(title="a")

        class _RequestReadingSerializer(serializers.ModelSerializer):
            who = serializers.SerializerMethodField()

            class Meta:
                model = Post
                fields = ("id", "who")

            def get_who(self, obj: Post) -> str:
                return str(self.context["request"])

        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_all_posts,
            output_serializer=_RequestReadingSerializer,
            output_serializer_context=lambda: {"request": "PROVIDER-WINS"},
        )
        result = dispatch_spec(spec, user=None, params={})
        rendered = render_spec_output(spec, list(result.value), many=True)
        assert [row["who"] for row in rendered] == ["PROVIDER-WINS"]

    def test_input_serializer_reads_request_from_the_baseline_context(self) -> None:
        class _OwnedPostIn(serializers.Serializer):
            title = serializers.CharField()

            def validate_title(self, value: str) -> str:
                return f"{value}-by-{self.context['request'].user}"

        def _create(*, data: dict[str, Any]) -> Post:
            return Post.objects.create(title=data["title"])

        spec = ServiceSpec(service=_create, input_serializer=_OwnedPostIn, atomic=False)
        offline = build_offline_context(user="alice")
        result = dispatch_spec(
            spec,
            user="alice",
            params={"title": "p"},
            request=offline.request,
            view=offline.view,
        )
        assert result.value.title == "p-by-alice"


@pytest.mark.django_db
class TestFilterDataOnTheReadPath:
    """``filter_data`` gives the FilterSet a different mapping than the callable.

    It reached only the *service* path until 0.31: a selector's ``filter_set``
    read ``params`` no matter what ``filter_data`` said, silently. That made it
    impossible for an off-HTTP transport to filter on anything the selector
    callable did not also declare as a keyword argument — which is precisely
    what an agent transport needs, since a spec's ``OrderingFilter`` is
    advertised to the model while no selector declares ``ordering``.
    """

    def test_filter_data_is_the_filter_source_when_given(self) -> None:
        author = Author.objects.create(name="A")
        Post.objects.create(title="shipped", author=author, published=True)
        Post.objects.create(title="draft", author=author, published=False)
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_all_posts,
            output_serializer=_PostSerializer,
            filter_set=_PublishedFilterSet,
        )

        result = dispatch_spec(
            spec,
            user=None,
            # The selector's pool says nothing about ``published``; only the
            # filter's own view of the arguments carries it.
            params={},
            filter_data={"published": "true"},
        )

        rendered = render_spec_output(spec, result.value, many=True)
        assert [row["title"] for row in rendered] == ["shipped"]

    def test_filter_data_replaces_params_rather_than_merging(self) -> None:
        """A value in ``params`` alone must not reach the FilterSet once
        ``filter_data`` is supplied — otherwise the two pools are one again and
        a caller cannot withhold an argument from the filter."""
        author = Author.objects.create(name="A")
        Post.objects.create(title="shipped", author=author, published=True)
        Post.objects.create(title="draft", author=author, published=False)
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_all_posts,
            output_serializer=_PostSerializer,
            filter_set=_PublishedFilterSet,
        )

        result = dispatch_spec(spec, user=None, params={"published": "true"}, filter_data={})

        rendered = render_spec_output(spec, result.value, many=True)
        assert [row["title"] for row in rendered] == ["shipped", "draft"]

    def test_omitting_filter_data_keeps_params_as_the_filter_source(self) -> None:
        author = Author.objects.create(name="A")
        Post.objects.create(title="shipped", author=author, published=True)
        Post.objects.create(title="draft", author=author, published=False)
        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_all_posts,
            output_serializer=_PostSerializer,
            filter_set=_PublishedFilterSet,
        )

        result = dispatch_spec(spec, user=None, params={"published": "false"})

        rendered = render_spec_output(spec, result.value, many=True)
        assert [row["title"] for row in rendered] == ["draft"]
