"""Tests for apply_input — in-memory, no DB writes."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rest_framework_services.mutations import apply_input
from rest_framework_services.types import UNSET
from tests.testapp.models import Author, Post


@dataclass
class _PartialPost:
    title: str = ""
    body: str = ""
    views: int = 0


@pytest.mark.django_db
class TestApplyInput:
    def test_dict_data_sets_changed_attrs_in_memory(self) -> None:
        post = Post(title="orig", body="old", views=0)
        result = apply_input(post, {"title": "new", "body": "old"})
        assert post.title == "new"
        assert post.body == "old"
        assert result.changed_fields == ("title",)
        assert result.created is False
        assert result.instance is post

    def test_dataclass_data(self) -> None:
        post = Post(title="orig")
        result = apply_input(post, _PartialPost(title="changed", body="b"))
        assert post.title == "changed"
        assert result.changed_fields == ("title", "body")

    def test_object_with_dict(self) -> None:
        class _Obj:
            def __init__(self) -> None:
                self.title = "via-dict"

        post = Post(title="x")
        result = apply_input(post, _Obj())
        assert post.title == "via-dict"
        assert result.changed_fields == ("title",)

    def test_none_data_returns_no_changes(self) -> None:
        post = Post(title="x")
        result = apply_input(post, None)
        assert result.changed_fields == ()
        assert bool(result) is False

    def test_unset_value_skipped(self) -> None:
        post = Post(title="orig", body="hello")
        result = apply_input(post, {"title": UNSET, "body": "hi"})
        assert post.title == "orig"
        assert result.changed_fields == ("body",)

    def test_field_map_translates_keys(self) -> None:
        post = Post(title="orig")
        result = apply_input(
            post,
            {"headline": "renamed"},
            field_map={"headline": "title"},
        )
        assert post.title == "renamed"
        assert result.changed_fields == ("title",)

    def test_exclude_fields_skips_input_keys(self) -> None:
        post = Post(title="orig", body="b")
        result = apply_input(
            post,
            {"title": "new", "body": "new-body"},
            exclude_fields=["body"],
        )
        assert post.title == "new"
        assert post.body == "b"
        assert result.changed_fields == ("title",)

    def test_unset_default_in_dataclass_skipped(self) -> None:
        @dataclass
        class _Sparse:
            title: object = UNSET
            body: str = "set"

        post = Post(title="orig", body="orig-body")
        result = apply_input(post, _Sparse())
        assert post.title == "orig"
        assert post.body == "set"
        assert result.changed_fields == ("body",)

    def test_unsupported_data_type_raises(self) -> None:
        post = Post(title="x")
        with pytest.raises(TypeError):
            apply_input(post, 42)

    def test_no_save_called(self) -> None:
        author = Author.objects.create(name="orig")
        apply_input(author, {"name": "new"})
        # Reload from DB; persisted value is unchanged.
        author.refresh_from_db()
        assert author.name == "orig"
