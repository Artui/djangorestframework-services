"""``render_for_audience`` — the render step, shaped for an agent audience.

``render_spec_output``'s own semantics are covered elsewhere; these pin what
this wrapper adds: the projection is applied, it can be supplied pre-built, and
the async twin does the same work off the event loop.
"""

from __future__ import annotations

import pytest
from django.db.models import QuerySet
from rest_framework import serializers

from rest_framework_services import (
    MARKING,
    FieldMarking,
    SelectorKind,
    SelectorSpec,
    arender_for_audience,
    build_audience_projection,
    render_for_audience,
    render_spec_output,
)
from tests.testapp.models import Post


class _PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ("id", "title")
        extra_kwargs = {
            "id": {"style": {MARKING: FieldMarking.hidden()}},
            "title": {"style": {MARKING: FieldMarking.label()}},
        }


class _PlainPostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ("id", "title")


def _all_posts() -> QuerySet[Post]:
    return Post.objects.all().order_by("id")


def _spec(serializer: type) -> SelectorSpec:
    return SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts, output_serializer=serializer)


@pytest.mark.django_db
class TestRenderForAgent:
    def test_applies_the_projection(self) -> None:
        Post.objects.create(title="a")
        spec = _spec(_PostSerializer)

        assert render_spec_output(spec, _all_posts(), many=True) == [{"id": 1, "title": "a"}]
        assert render_for_audience(spec, _all_posts(), many=True) == [{"title": "a"}]

    def test_accepts_a_prebuilt_projection(self) -> None:
        """The registration-time path: derive once, reuse per call."""
        Post.objects.create(title="a")
        projection = build_audience_projection(_PostSerializer)

        rendered = render_for_audience(
            _spec(_PostSerializer), _all_posts(), many=True, projection=projection
        )

        assert rendered == [{"title": "a"}]

    def test_unmarked_serializer_renders_unchanged(self) -> None:
        Post.objects.create(title="a")
        spec = _spec(_PlainPostSerializer)

        assert render_for_audience(spec, _all_posts(), many=True) == [{"id": 1, "title": "a"}]

    def test_serializerless_spec_passes_the_value_through(self) -> None:
        Post.objects.create(title="a")
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=_all_posts)

        assert list(render_for_audience(spec, _all_posts(), many=True)) == list(_all_posts())


@pytest.mark.django_db(transaction=True)
class TestARenderForAgent:
    async def test_matches_the_sync_twin_off_the_loop(self) -> None:
        await Post.objects.acreate(title="a")
        spec = _spec(_PostSerializer)

        assert await arender_for_audience(spec, _all_posts(), many=True) == [{"title": "a"}]
