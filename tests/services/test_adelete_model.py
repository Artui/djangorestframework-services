"""Tests for ``adelete_model``."""

from __future__ import annotations

import pytest

from rest_framework_services import adelete_model
from tests.testapp.models import Author


@pytest.mark.django_db(transaction=True)
class TestAmodelDeleteService:
    async def test_happy_path_deletes_instance(self) -> None:
        author = await Author.objects.acreate(name="Ada")
        pk = author.pk
        service = adelete_model(Author)
        result = await service(instance=author)
        assert result is None
        assert not await Author.objects.filter(pk=pk).aexists()

    async def test_soft_delete_hook_replaces_delete(self) -> None:
        author = await Author.objects.acreate(name="Ada")
        pk = author.pk
        calls: list[Author] = []

        async def _archive(inst: Author) -> None:
            calls.append(inst)

        service = adelete_model(Author, soft_delete=_archive)
        await service(instance=author)
        assert calls == [author]
        assert await Author.objects.filter(pk=pk).aexists()

    async def test_returned_callable_absorbs_arbitrary_extras(self) -> None:
        author = await Author.objects.acreate(name="Ada")
        pk = author.pk
        service = adelete_model(Author)
        await service(
            instance=author,
            request=object(),
            user=object(),
            tenant_id=42,
            anything_else="ignored",
        )
        assert not await Author.objects.filter(pk=pk).aexists()
