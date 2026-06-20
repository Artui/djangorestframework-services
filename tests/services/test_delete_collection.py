"""``delete_collection`` / ``adelete_collection`` default bulk-delete services."""

from __future__ import annotations

from typing import Any

import pytest
from django.db.models import QuerySet

from rest_framework_services import adelete_collection, delete_collection
from tests.testapp.models import Post


@pytest.mark.django_db
class TestDeleteCollection:
    def test_deletes_the_collection(self) -> None:
        Post.objects.create(title="a")
        Post.objects.create(title="b")
        service = delete_collection(Post)
        assert service(collection=Post.objects.all()) is None
        assert Post.objects.count() == 0

    def test_soft_delete_hook(self) -> None:
        Post.objects.create(title="a", published=True)
        captured: dict[str, Any] = {}

        def archive(collection: QuerySet[Post]) -> None:
            captured["n"] = collection.update(published=False)

        service = delete_collection(Post, soft_delete=archive)
        service(collection=Post.objects.all())
        assert captured["n"] == 1
        assert Post.objects.count() == 1  # not deleted
        assert not Post.objects.filter(published=True).exists()


@pytest.mark.django_db(transaction=True)
class TestADeleteCollection:
    async def test_deletes_the_collection(self) -> None:
        await Post.objects.acreate(title="a")
        await Post.objects.acreate(title="b")
        service = adelete_collection(Post)
        assert await service(collection=Post.objects.all()) is None
        assert await Post.objects.acount() == 0

    async def test_soft_delete_hook(self) -> None:
        await Post.objects.acreate(title="a", published=True)
        captured: dict[str, Any] = {}

        async def archive(collection: QuerySet[Post]) -> None:
            captured["n"] = await collection.aupdate(published=False)

        service = adelete_collection(Post, soft_delete=archive)
        await service(collection=Post.objects.all())
        assert captured["n"] == 1
        assert await Post.objects.acount() == 1
