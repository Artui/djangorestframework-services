"""Tests for ``update_model``."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rest_framework_services import update_model
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


@pytest.mark.django_db
class TestModelUpdateService:
    def test_happy_path_updates_in_place(self) -> None:
        author = Author.objects.create(name="Ada")
        service = update_model(Author)
        updated = service(instance=author, data=_AuthorIn(name="Grace"))
        assert updated is author
        author.refresh_from_db()
        assert author.name == "Grace"

    def test_returns_only_the_instance(self) -> None:
        author = Author.objects.create(name="Ada")
        service = update_model(Author)
        result = service(instance=author, data=_AuthorIn(name="Grace"))
        assert isinstance(result, Author)
        assert not hasattr(result, "changed_fields")

    def test_field_map_renames_input_keys(self) -> None:
        @dataclass
        class _AuthorWithDisplayName:
            display_name: str

        author = Author.objects.create(name="Ada")
        service = update_model(Author, field_map={"display_name": "name"})
        service(instance=author, data=_AuthorWithDisplayName(display_name="Grace"))
        author.refresh_from_db()
        assert author.name == "Grace"

    def test_exclude_fields_drops_keys(self) -> None:
        @dataclass
        class _AuthorWithExtra:
            name: str
            internal: str

        author = Author.objects.create(name="Ada")
        service = update_model(Author, exclude_fields=["internal"])
        service(instance=author, data=_AuthorWithExtra(name="Edsger", internal="x"))
        author.refresh_from_db()
        assert author.name == "Edsger"

    def test_m2m_as_static_mapping(self) -> None:
        tag = Tag.objects.create(name="python")
        post = Post.objects.create(title="t", body="b")
        service = update_model(Post, m2m={"tags": [tag]})
        service(instance=post, data=_PostIn(title="t2", body="b2"))
        assert list(post.tags.all()) == [tag]

    def test_m2m_as_callable_from_data(self) -> None:
        tag = Tag.objects.create(name="django")
        post = Post.objects.create(title="t", body="b")
        service = update_model(
            Post,
            exclude_fields=["tags"],
            m2m=lambda data: {"tags": data.tags},
        )
        service(instance=post, data=_PostWithTagsIn(title="t2", body="b2", tags=[tag]))
        assert list(post.tags.all()) == [tag]

    def test_update_fields_true_uses_changed_field_save(self) -> None:
        author = Author.objects.create(name="Ada")
        service = update_model(Author, update_fields=True)
        service(instance=author, data=_AuthorIn(name="Grace"))
        author.refresh_from_db()
        assert author.name == "Grace"

    def test_update_fields_false_full_save(self) -> None:
        author = Author.objects.create(name="Ada")
        service = update_model(Author, update_fields=False)
        service(instance=author, data=_AuthorIn(name="Grace"))
        author.refresh_from_db()
        assert author.name == "Grace"

    def test_update_fields_explicit_list(self) -> None:
        author = Author.objects.create(name="Ada")
        service = update_model(Author, update_fields=["name"])
        service(instance=author, data=_AuthorIn(name="Grace"))
        author.refresh_from_db()
        assert author.name == "Grace"

    def test_returned_callable_absorbs_arbitrary_extras(self) -> None:
        author = Author.objects.create(name="Ada")
        service = update_model(Author)
        service(
            instance=author,
            data=_AuthorIn(name="Margaret"),
            request=object(),
            user=object(),
            tenant_id=42,
        )
        author.refresh_from_db()
        assert author.name == "Margaret"
