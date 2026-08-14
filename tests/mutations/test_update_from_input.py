"""Tests for update_from_input."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from rest_framework_services.mutations import update_from_input
from rest_framework_services.types import UNSET
from tests.testapp.models import Author, Post, Tag, Timestamped


@pytest.mark.django_db
class TestUpdateFromInput:
    def test_no_changes_returns_empty_result(self) -> None:
        author = Author.objects.create(name="same")
        result = update_from_input(author, {"name": "same"})
        assert result.changed_fields == ()
        assert bool(result) is False
        assert result.created is False

    def test_changes_persisted(self) -> None:
        author = Author.objects.create(name="orig")
        result = update_from_input(author, {"name": "new"})
        author.refresh_from_db()
        assert author.name == "new"
        assert result.changed_fields == ("name",)

    def test_change_records_old_and_new(self) -> None:
        author = Author.objects.create(name="orig")
        result = update_from_input(author, {"name": "new"})
        change = result.get_field_change("name")
        assert change is not None
        assert change.old == "orig"
        assert change.new == "new"

    def test_unset_value_skipped(self) -> None:
        post = Post.objects.create(title="orig", body="body")
        result = update_from_input(post, {"title": UNSET, "body": "new-body"})
        post.refresh_from_db()
        assert post.title == "orig"
        assert post.body == "new-body"
        assert result.changed_fields == ("body",)

    def test_field_map(self) -> None:
        post = Post.objects.create(title="orig")
        result = update_from_input(
            post,
            {"headline": "renamed"},
            field_map={"headline": "title"},
        )
        post.refresh_from_db()
        assert post.title == "renamed"
        assert result.changed_fields == ("title",)

    def test_exclude_fields(self) -> None:
        post = Post.objects.create(title="orig", body="b")
        result = update_from_input(
            post,
            {"title": "new", "body": "ignored"},
            exclude_fields=["body"],
        )
        post.refresh_from_db()
        assert post.title == "new"
        assert post.body == "b"
        assert result.changed_fields == ("title",)

    def test_dataclass_input(self) -> None:
        @dataclass
        class _PartialPost:
            title: str = "fresh"
            body: object = UNSET

        post = Post.objects.create(title="orig", body="orig-body")
        result = update_from_input(post, _PartialPost())
        post.refresh_from_db()
        assert post.title == "fresh"
        assert post.body == "orig-body"
        assert result.changed_fields == ("title",)

    def test_save_only_calls_with_changed_update_fields(self) -> None:
        post = Post.objects.create(title="orig", body="b", views=1)
        with patch.object(Post, "save", autospec=True) as mock_save:
            update_from_input(post, {"title": "x", "body": "b"})
            assert mock_save.called
            kwargs = mock_save.call_args.kwargs
            assert kwargs == {"update_fields": ["title"]}

    def test_save_skipped_when_no_changes_default(self) -> None:
        post = Post.objects.create(title="orig")
        with patch.object(Post, "save", autospec=True) as mock_save:
            update_from_input(post, {"title": "orig"})
            assert mock_save.called is False

    def test_full_save_when_update_fields_false(self) -> None:
        post = Post.objects.create(title="orig")
        with patch.object(Post, "save", autospec=True) as mock_save:
            update_from_input(post, {"title": "orig"}, update_fields=False)
            assert mock_save.called
            assert mock_save.call_args.kwargs == {}

    def test_explicit_update_fields_list(self) -> None:
        post = Post.objects.create(title="orig", body="b")
        with patch.object(Post, "save", autospec=True) as mock_save:
            update_from_input(post, {"title": "x"}, update_fields=["title", "body"])
            kwargs = mock_save.call_args.kwargs
            assert kwargs == {"update_fields": ["title", "body"]}

    def test_m2m_change_detected(self) -> None:
        red = Tag.objects.create(name="red")
        blue = Tag.objects.create(name="blue")
        post = Post.objects.create(title="t")
        post.tags.set([red])

        result = update_from_input(post, None, m2m={"tags": [blue]})
        assert set(post.tags.values_list("pk", flat=True)) == {blue.pk}
        assert "tags" in result.changed_fields
        change = result.get_field_change("tags")
        assert change is not None
        assert change.old == [red.pk]

    def test_m2m_no_change_when_same_set(self) -> None:
        red = Tag.objects.create(name="red")
        post = Post.objects.create(title="t")
        post.tags.set([red])

        result = update_from_input(post, None, m2m={"tags": [red]})
        assert result.changed_fields == ()

    def test_m2m_pks_compared_against_current(self) -> None:
        red = Tag.objects.create(name="red")
        blue = Tag.objects.create(name="blue")
        post = Post.objects.create(title="t")
        post.tags.set([red])

        result = update_from_input(post, None, m2m={"tags": [red.pk, blue.pk]})
        assert set(post.tags.values_list("pk", flat=True)) == {red.pk, blue.pk}
        assert "tags" in result.changed_fields

    def test_auto_now_field_included_in_update_fields(self) -> None:
        obj = Timestamped.objects.create(title="orig")
        with patch.object(Timestamped, "save", autospec=True) as mock_save:
            update_from_input(obj, {"title": "new"})
            kwargs = mock_save.call_args.kwargs
            assert "updated_at" in kwargs["update_fields"]
            assert "title" in kwargs["update_fields"]

    def test_auto_now_add_field_not_included_in_update_fields(self) -> None:
        obj = Timestamped.objects.create(title="orig")
        with patch.object(Timestamped, "save", autospec=True) as mock_save:
            update_from_input(obj, {"title": "new"})
            assert "created_at" not in mock_save.call_args.kwargs["update_fields"]

    def test_explicit_update_fields_not_modified_by_auto_now(self) -> None:
        obj = Timestamped.objects.create(title="orig")
        with patch.object(Timestamped, "save", autospec=True) as mock_save:
            update_from_input(obj, {"title": "new"}, update_fields=["title"])
            assert mock_save.call_args.kwargs == {"update_fields": ["title"]}

    def test_auto_now_field_actually_updated_in_db(self) -> None:
        obj = Timestamped.objects.create(title="orig")
        # Force updated_at to a value one hour in the past to ensure a measurable diff.
        old_ts = timezone.now() - timedelta(hours=1)
        Timestamped.objects.filter(pk=obj.pk).update(updated_at=old_ts)
        obj.refresh_from_db()

        update_from_input(obj, {"title": "new"})
        obj.refresh_from_db()
        assert obj.updated_at > old_ts

    def test_the_new_auto_now_value_needs_no_reload_to_be_read(self) -> None:
        # Django's pre_save puts the new timestamp on the instance, so the
        # returned one already carries it. The docs draw the line between
        # values like this and the ones only a reload or a re-fetch produces
        # (an F() expression, a fetch-time annotation), so pin this end of it.
        obj = Timestamped.objects.create(title="orig")
        Timestamped.objects.filter(pk=obj.pk).update(updated_at=timezone.now() - timedelta(hours=1))
        obj.refresh_from_db()

        result = update_from_input(obj, {"title": "new"})

        assert result.instance.updated_at == Timestamped.objects.get(pk=obj.pk).updated_at
