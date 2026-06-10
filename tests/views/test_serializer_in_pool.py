"""Tests for the bound input serializer seeded into the service kwarg pool."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ServiceCreateView,
    ServiceSpec,
    ServiceUpdateView,
)
from tests.testapp.models import Post, Tag

factory = APIRequestFactory()


class _PostWithTagsSerializer(serializers.ModelSerializer):
    """Nested-write serializer — persistence lives on the serializer."""

    tags = serializers.ListField(child=serializers.CharField(), required=False)

    class Meta:
        model = Post
        fields = ("id", "title", "tags")

    def create(self, validated_data: dict[str, Any]) -> Post:
        tag_names: list[str] = validated_data.pop("tags", [])
        post = Post.objects.create(**validated_data)
        for name in tag_names:
            post.tags.add(Tag.objects.create(name=name))
        return post

    def update(self, instance: Post, validated_data: dict[str, Any]) -> Post:
        tag_names: list[str] = validated_data.pop("tags", [])
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        for name in tag_names:
            instance.tags.add(Tag.objects.create(name=name))
        return instance


def _save_via_serializer(*, serializer: serializers.ModelSerializer) -> Post:
    return serializer.save()


@pytest.mark.django_db
class TestSerializerInPool:
    def test_create_service_receives_bound_validated_serializer(self) -> None:
        captured: dict[str, Any] = {}

        def _service(*, serializer: Any) -> Post:
            captured["serializer"] = serializer
            captured["instance_before_save"] = serializer.instance
            return serializer.save()

        class _View(ServiceCreateView):
            spec = ServiceSpec(service=_service, input_serializer=_PostWithTagsSerializer)

        response = _View.as_view()(factory.post("/", {"title": "hello"}, format="json"))
        assert response.status_code == 201
        bound = captured["serializer"]
        assert isinstance(bound, _PostWithTagsSerializer)
        assert bound.validated_data == {"title": "hello"}
        assert captured["instance_before_save"] is None

    def test_nested_write_save_round_trip(self) -> None:
        class _View(ServiceCreateView):
            spec = ServiceSpec(
                service=_save_via_serializer, input_serializer=_PostWithTagsSerializer
            )

        response = _View.as_view()(
            factory.post("/", {"title": "tagged", "tags": ["a", "b"]}, format="json")
        )
        assert response.status_code == 201
        post = Post.objects.get(title="tagged")
        assert sorted(post.tags.values_list("name", flat=True)) == ["a", "b"]

    def test_update_serializer_is_instance_bound_and_save_updates(self) -> None:
        captured: dict[str, Any] = {}

        def _service(*, serializer: Any) -> Post:
            captured["instance"] = serializer.instance
            return serializer.save()

        class _View(ServiceUpdateView):
            queryset = Post.objects.all()
            spec = ServiceSpec(service=_service, input_serializer=_PostWithTagsSerializer)

        post = Post.objects.create(title="orig")
        response = _View.as_view()(
            factory.put("/", {"title": "renamed"}, format="json"), pk=post.pk
        )
        assert response.status_code == 200
        assert captured["instance"] == post
        post.refresh_from_db()
        assert post.title == "renamed"
        # ``serializer.save()`` took the update path — no second row created.
        assert Post.objects.count() == 1

    def test_service_not_declaring_serializer_is_unaffected(self) -> None:
        def _service(*, data: dict[str, Any]) -> Post:
            return Post.objects.create(title=data["title"])

        class _View(ServiceCreateView):
            spec = ServiceSpec(service=_service, input_serializer=_PostWithTagsSerializer)

        response = _View.as_view()(factory.post("/", {"title": "plain"}, format="json"))
        assert response.status_code == 201
        assert Post.objects.filter(title="plain").exists()

    def test_serializer_seed_cannot_be_poisoned_by_request_data(self) -> None:
        captured: dict[str, Any] = {}

        def _service(*, serializer: Any) -> Post:
            captured["serializer"] = serializer
            return serializer.save()

        class _View(ServiceCreateView):
            spec = ServiceSpec(service=_service, input_serializer=_PostWithTagsSerializer)

        response = _View.as_view()(
            factory.post("/", {"title": "x", "serializer": "evil"}, format="json")
        )
        assert response.status_code == 201
        assert isinstance(captured["serializer"], _PostWithTagsSerializer)
