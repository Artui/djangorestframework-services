"""Tests for ``create_model``."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rest_framework_services import create_model
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


@pytest.mark.django_db
class TestModelCreateService:
    def test_happy_path_builds_and_persists(self) -> None:
        service = create_model(Author)
        author = service(data=_AuthorIn(name="Ada"))
        assert isinstance(author, Author)
        assert author.pk is not None
        assert Author.objects.filter(name="Ada").exists()

    def test_returns_only_the_instance_not_a_change_result(self) -> None:
        service = create_model(Author)
        author = service(data=_AuthorIn(name="Linus"))
        assert isinstance(author, Author)
        assert not hasattr(author, "changed_fields")

    def test_field_map_renames_input_keys(self) -> None:
        @dataclass
        class _AuthorWithDisplayName:
            display_name: str

        service = create_model(Author, field_map={"display_name": "name"})
        author = service(data=_AuthorWithDisplayName(display_name="Grace"))
        assert author.name == "Grace"

    def test_exclude_fields_drops_keys(self) -> None:
        @dataclass
        class _AuthorWithExtra:
            name: str
            internal_note: str

        service = create_model(Author, exclude_fields=["internal_note"])
        author = service(data=_AuthorWithExtra(name="Edsger", internal_note="ignored"))
        assert author.name == "Edsger"
        assert not hasattr(author, "internal_note")

    def test_m2m_as_static_mapping(self) -> None:
        tag = Tag.objects.create(name="python")
        service = create_model(Post, m2m={"tags": [tag]})
        post = service(data=_PostIn(title="t", body="b"))
        assert list(post.tags.all()) == [tag]

    def test_m2m_as_callable_from_data(self) -> None:
        tag = Tag.objects.create(name="django")
        service = create_model(
            Post,
            exclude_fields=["tags"],
            m2m=lambda data: {"tags": data.tags},
        )
        post = service(data=_PostWithTagsIn(title="t", body="b", tags=[tag]))
        assert list(post.tags.all()) == [tag]

    def test_returned_callable_absorbs_arbitrary_extras(self) -> None:
        """Proves the closure matches the default-``ExtraT`` Protocol shape."""
        service = create_model(Author)
        author = service(
            data=_AuthorIn(name="Margaret"),
            request=object(),
            user=object(),
            tenant_id=42,
            anything_else="ignored",
        )
        assert author.name == "Margaret"
