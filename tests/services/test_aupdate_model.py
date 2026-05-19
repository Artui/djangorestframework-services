"""Tests for ``aupdate_model``."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rest_framework_services import aupdate_model
from tests.testapp.models import Author, Post, Tag


@dataclass
class _AuthorIn:
    name: str


@dataclass
class _PostIn:
    title: str
    body: str


@dataclass
class _PostWithTagsIn:
    title: str
    body: str
    tags: list[Tag]


@pytest.mark.django_db(transaction=True)
class TestAmodelUpdateService:
    async def test_happy_path_updates_in_place(self) -> None:
        author = await Author.objects.acreate(name="Ada")
        service = aupdate_model(Author)
        updated = await service(instance=author, data=_AuthorIn(name="Grace"))
        assert updated is author
        await author.arefresh_from_db()
        assert author.name == "Grace"

    async def test_field_map_renames_input_keys(self) -> None:
        @dataclass
        class _AuthorWithDisplayName:
            display_name: str

        author = await Author.objects.acreate(name="Ada")
        service = aupdate_model(Author, field_map={"display_name": "name"})
        await service(instance=author, data=_AuthorWithDisplayName(display_name="Grace"))
        await author.arefresh_from_db()
        assert author.name == "Grace"

    async def test_exclude_fields_drops_keys(self) -> None:
        @dataclass
        class _AuthorWithExtra:
            name: str
            internal: str

        author = await Author.objects.acreate(name="Ada")
        service = aupdate_model(Author, exclude_fields=["internal"])
        await service(instance=author, data=_AuthorWithExtra(name="Edsger", internal="x"))
        await author.arefresh_from_db()
        assert author.name == "Edsger"

    async def test_m2m_as_static_mapping(self) -> None:
        tag = await Tag.objects.acreate(name="python")
        post = await Post.objects.acreate(title="t", body="b")
        service = aupdate_model(Post, m2m={"tags": [tag]})
        await service(instance=post, data=_PostIn(title="t2", body="b2"))
        tags = [t async for t in post.tags.all()]
        assert tags == [tag]

    async def test_m2m_as_callable_from_data(self) -> None:
        tag = await Tag.objects.acreate(name="django")
        post = await Post.objects.acreate(title="t", body="b")
        service = aupdate_model(
            Post,
            exclude_fields=["tags"],
            m2m=lambda data: {"tags": data.tags},
        )
        await service(instance=post, data=_PostWithTagsIn(title="t2", body="b2", tags=[tag]))
        tags = [t async for t in post.tags.all()]
        assert tags == [tag]

    async def test_update_fields_true(self) -> None:
        author = await Author.objects.acreate(name="Ada")
        service = aupdate_model(Author, update_fields=True)
        await service(instance=author, data=_AuthorIn(name="Grace"))
        await author.arefresh_from_db()
        assert author.name == "Grace"

    async def test_update_fields_false(self) -> None:
        author = await Author.objects.acreate(name="Ada")
        service = aupdate_model(Author, update_fields=False)
        await service(instance=author, data=_AuthorIn(name="Grace"))
        await author.arefresh_from_db()
        assert author.name == "Grace"

    async def test_update_fields_explicit_list(self) -> None:
        author = await Author.objects.acreate(name="Ada")
        service = aupdate_model(Author, update_fields=["name"])
        await service(instance=author, data=_AuthorIn(name="Grace"))
        await author.arefresh_from_db()
        assert author.name == "Grace"

    async def test_returned_callable_absorbs_arbitrary_extras(self) -> None:
        author = await Author.objects.acreate(name="Ada")
        service = aupdate_model(Author)
        await service(
            instance=author,
            data=_AuthorIn(name="Margaret"),
            request=object(),
            user=object(),
            tenant_id=42,
        )
        await author.arefresh_from_db()
        assert author.name == "Margaret"
