"""``arender_spec_output`` — the render step, off the event loop.

The sync twin's semantics are covered in ``test_dispatch_spec.py``; these pin
the two things that are this function's reason to exist: the result is the same,
and every piece of ORM work it does (serializing rows, the context provider's
query, the no-serializer list coercion) survives being called from a coroutine.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db.models import QuerySet
from rest_framework import serializers

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    adispatch_spec,
    arender_spec_output,
    build_offline_context,
)
from tests.testapp.models import Post


class _PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ("id", "title")


def _all_posts() -> QuerySet[Post]:
    return Post.objects.all().order_by("id")


def _posts_by_pk(*, pk: int) -> QuerySet[Post]:
    return Post.objects.filter(pk=pk)


@pytest.mark.django_db(transaction=True)
class TestARenderSpecOutput:
    async def test_renders_the_lazy_queryset_adispatch_returns(self) -> None:
        """The pairing that motivates it: a LIST result is deliberately lazy."""
        await Post.objects.acreate(title="a")
        await Post.objects.acreate(title="b")
        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=_all_posts, output_serializer=_PostSerializer
        )
        result = await adispatch_spec(spec, user=None, params={})
        rendered = await arender_spec_output(spec, result.value, many=True)
        assert [row["title"] for row in rendered] == ["a", "b"]

    async def test_renders_a_single_instance(self) -> None:
        post = await Post.objects.acreate(title="a")
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_posts_by_pk, output_serializer=_PostSerializer
        )
        result = await adispatch_spec(spec, user=None, params={"pk": post.pk})
        assert await arender_spec_output(spec, result.value) == {"id": post.pk, "title": "a"}

    async def test_output_context_provider_may_query(self) -> None:
        await Post.objects.acreate(title="a")

        class _CountingSerializer(serializers.ModelSerializer):
            total = serializers.SerializerMethodField()

            class Meta:
                model = Post
                fields = ("id", "total")

            def get_total(self, _: Post) -> int:
                return self.context["total"]

        def provider(*, page: Any) -> dict[str, Any]:
            # The documented shape: one batched query keyed on the page.
            return {"total": Post.objects.filter(pk__in=[p.pk for p in page]).count()}

        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_all_posts,
            output_serializer=_CountingSerializer,
            output_serializer_context=provider,
        )
        result = await adispatch_spec(spec, user=None, params={})
        rendered = await arender_spec_output(
            spec, result.value, many=True, extras={"page": result.value}
        )
        assert [row["total"] for row in rendered] == [1]

    async def test_baseline_context_reaches_the_serializer(self) -> None:
        await Post.objects.acreate(title="a")

        class _RequestReadingSerializer(serializers.ModelSerializer):
            who = serializers.SerializerMethodField()

            class Meta:
                model = Post
                fields = ("id", "who")

            def get_who(self, _: Post) -> str:
                return str(self.context["request"].user)

        spec = SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_all_posts,
            output_serializer=_RequestReadingSerializer,
        )
        offline = build_offline_context(user="alice")
        result = await adispatch_spec(
            spec, user="alice", params={}, request=offline.request, view=offline.view
        )
        rendered = await arender_spec_output(
            spec, result.value, many=True, request=offline.request, view=offline.view
        )
        assert [row["who"] for row in rendered] == ["alice"]

    async def test_passthrough_list_coercion_evaluates_the_queryset(self) -> None:
        """No output serializer: ``many=True`` still evaluates — that is ORM work."""
        await Post.objects.acreate(title="a")
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts)
        result = await adispatch_spec(spec, user=None, params={})
        rendered = await arender_spec_output(spec, result.value, many=True)
        assert [post.title for post in rendered] == ["a"]

    async def test_passthrough_scalar(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: 7)
        result = await adispatch_spec(spec, user=None, params={})
        assert await arender_spec_output(spec, result.value) == 7
