"""Tests for ``acreate_model``."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rest_framework_services import acreate_model
from tests.testapp.models import Author, Post, Tag


@dataclass
class _AuthorIn:
    name: str


@dataclass
class _PostIn:
    title: str
    body: str = ""


@dataclass
class _PostWithTagsIn:
    title: str
    body: str
    tags: list[Tag]


@pytest.mark.django_db(transaction=True)
class TestAmodelCreateService:
    async def test_happy_path_builds_and_persists(self) -> None:
        service = acreate_model(Author)
        author = await service(data=_AuthorIn(name="Ada"))
        assert isinstance(author, Author)
        assert author.pk is not None
        assert await Author.objects.filter(name="Ada").aexists()

    async def test_field_map_renames_input_keys(self) -> None:
        @dataclass
        class _AuthorWithDisplayName:
            display_name: str

        service = acreate_model(Author, field_map={"display_name": "name"})
        author = await service(data=_AuthorWithDisplayName(display_name="Grace"))
        assert author.name == "Grace"

    async def test_exclude_fields_drops_keys(self) -> None:
        @dataclass
        class _AuthorWithExtra:
            name: str
            internal: str

        service = acreate_model(Author, exclude_fields=["internal"])
        author = await service(data=_AuthorWithExtra(name="Edsger", internal="x"))
        assert author.name == "Edsger"

    async def test_m2m_as_static_mapping(self) -> None:
        tag = await Tag.objects.acreate(name="python")
        service = acreate_model(Post, m2m={"tags": [tag]})
        post = await service(data=_PostIn(title="t", body="b"))
        tag_names = [t async for t in post.tags.all()]
        assert tag_names == [tag]

    async def test_m2m_as_callable_from_data(self) -> None:
        tag = await Tag.objects.acreate(name="django")
        service = acreate_model(
            Post,
            exclude_fields=["tags"],
            m2m=lambda data: {"tags": data.tags},
        )
        post = await service(data=_PostWithTagsIn(title="t", body="b", tags=[tag]))
        tag_names = [t async for t in post.tags.all()]
        assert tag_names == [tag]

    async def test_returned_callable_absorbs_arbitrary_extras(self) -> None:
        service = acreate_model(Author)
        author = await service(
            data=_AuthorIn(name="Margaret"),
            request=object(),
            user=object(),
            tenant_id=42,
            anything_else="ignored",
        )
        assert author.name == "Margaret"
