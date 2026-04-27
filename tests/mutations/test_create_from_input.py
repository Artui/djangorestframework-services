"""Tests for create_from_input."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rest_framework_services.mutations import create_from_input
from rest_framework_services.types import UNSET
from rest_framework_services.types.unset import _Unset
from tests.testapp.models import Author, Post, Tag


@dataclass
class _PostInput:
    title: str
    body: str = "default-body"


@pytest.mark.django_db
class TestCreateFromInput:
    def test_creates_persisted_instance(self) -> None:
        result = create_from_input(Author, {"name": "Ada"})
        assert result.created is True
        assert result.instance.pk is not None
        assert Author.objects.filter(name="Ada").exists()

    def test_changes_record_creation_fields(self) -> None:
        result = create_from_input(Author, {"name": "Ada"})
        assert result.changed_fields == ("name",)
        change = result.get_field_change("name")
        assert change is not None
        assert isinstance(change.old, _Unset)
        assert change.new == "Ada"

    def test_dataclass_input(self) -> None:
        result = create_from_input(Post, _PostInput(title="hi"))
        assert result.instance.title == "hi"
        assert result.instance.body == "default-body"
        assert "title" in result.changed_fields
        assert "body" in result.changed_fields

    def test_unset_fields_omitted_from_create(self) -> None:
        @dataclass
        class _Sparse:
            title: str = "abc"
            body: object = UNSET

        result = create_from_input(Post, _Sparse())
        assert result.changed_fields == ("title",)
        # Default DB value used since `body` not assigned.
        assert result.instance.body == ""

    def test_field_map(self) -> None:
        result = create_from_input(
            Post,
            {"headline": "Mapped"},
            field_map={"headline": "title"},
        )
        assert result.instance.title == "Mapped"
        assert result.changed_fields == ("title",)

    def test_exclude_fields(self) -> None:
        result = create_from_input(
            Author,
            {"name": "x", "drop_me": "secret"},
            exclude_fields=["drop_me"],
        )
        assert result.instance.name == "x"
        assert result.changed_fields == ("name",)

    def test_m2m_assignment_after_save(self) -> None:
        red = Tag.objects.create(name="red")
        blue = Tag.objects.create(name="blue")
        result = create_from_input(
            Post,
            {"title": "tagged"},
            m2m={"tags": [red, blue]},
        )
        assert set(result.instance.tags.values_list("pk", flat=True)) == {red.pk, blue.pk}
        assert "tags" in result.changed_fields
        tags_change = result.get_field_change("tags")
        assert tags_change is not None
        assert isinstance(tags_change.old, _Unset)

    def test_m2m_with_pks(self) -> None:
        red = Tag.objects.create(name="red")
        result = create_from_input(
            Post,
            {"title": "by-id"},
            m2m={"tags": [red.pk]},
        )
        assert list(result.instance.tags.values_list("pk", flat=True)) == [red.pk]

    def test_m2m_none_treated_as_empty(self) -> None:
        # Passing None for an m2m attr should set an empty relation, not error.
        result = create_from_input(
            Post,
            {"title": "no-tags"},
            m2m={"tags": None},
        )
        assert list(result.instance.tags.all()) == []
        # The change is still recorded for creates.
        assert "tags" in result.changed_fields

    def test_foreign_key_assignment(self) -> None:
        author = Author.objects.create(name="a")
        result = create_from_input(Post, {"title": "x", "author": author})
        assert result.instance.author == author
