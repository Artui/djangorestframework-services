"""Tests for aupdate_from_input."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from rest_framework_services.mutations import aupdate_from_input
from rest_framework_services.types import UNSET
from tests.testapp.models import Author, Post, Tag


@pytest.mark.django_db(transaction=True)
class TestAUpdateFromInput:
    async def test_no_changes(self) -> None:
        author = await Author.objects.acreate(name="same")
        result = await aupdate_from_input(author, {"name": "same"})
        assert result.changed_fields == ()

    async def test_changes_persisted(self) -> None:
        author = await Author.objects.acreate(name="orig")
        result = await aupdate_from_input(author, {"name": "new"})
        await author.arefresh_from_db()
        assert author.name == "new"
        assert result.changed_fields == ("name",)

    async def test_unset_value_skipped(self) -> None:
        post = await Post.objects.acreate(title="orig", body="body")
        await aupdate_from_input(post, {"title": UNSET, "body": "new"})
        await post.arefresh_from_db()
        assert post.title == "orig"
        assert post.body == "new"

    async def test_field_map(self) -> None:
        post = await Post.objects.acreate(title="orig")
        await aupdate_from_input(
            post,
            {"headline": "renamed"},
            field_map={"headline": "title"},
        )
        await post.arefresh_from_db()
        assert post.title == "renamed"

    async def test_exclude_fields(self) -> None:
        post = await Post.objects.acreate(title="orig", body="b")
        await aupdate_from_input(
            post,
            {"title": "new", "body": "ignored"},
            exclude_fields=["body"],
        )
        await post.arefresh_from_db()
        assert post.title == "new"
        assert post.body == "b"

    async def test_save_called_with_update_fields(self) -> None:
        post = await Post.objects.acreate(title="orig", body="b")
        with patch.object(Post, "asave", new_callable=AsyncMock) as mock_save:
            await aupdate_from_input(post, {"title": "x", "body": "b"})
            assert mock_save.called
            assert mock_save.call_args.kwargs == {"update_fields": ["title"]}

    async def test_save_skipped_when_no_changes(self) -> None:
        post = await Post.objects.acreate(title="orig")
        with patch.object(Post, "asave", new_callable=AsyncMock) as mock_save:
            await aupdate_from_input(post, {"title": "orig"})
            assert mock_save.called is False

    async def test_full_asave_when_update_fields_false(self) -> None:
        post = await Post.objects.acreate(title="orig")
        with patch.object(Post, "asave", new_callable=AsyncMock) as mock_save:
            await aupdate_from_input(post, {"title": "orig"}, update_fields=False)
            assert mock_save.called
            assert mock_save.call_args.kwargs == {}

    async def test_explicit_update_fields_list(self) -> None:
        post = await Post.objects.acreate(title="orig", body="b")
        with patch.object(Post, "asave", new_callable=AsyncMock) as mock_save:
            await aupdate_from_input(
                post,
                {"title": "x"},
                update_fields=["title", "body"],
            )
            assert mock_save.call_args.kwargs == {"update_fields": ["title", "body"]}

    async def test_m2m_change_detected(self) -> None:
        red = await Tag.objects.acreate(name="red")
        blue = await Tag.objects.acreate(name="blue")
        post = await Post.objects.acreate(title="t")
        await post.tags.aset([red])

        result = await aupdate_from_input(post, None, m2m={"tags": [blue]})
        pks = {pk async for pk in post.tags.values_list("pk", flat=True)}
        assert pks == {blue.pk}
        assert "tags" in result.changed_fields

    async def test_m2m_no_change_when_same_set(self) -> None:
        red = await Tag.objects.acreate(name="red")
        post = await Post.objects.acreate(title="t")
        await post.tags.aset([red])

        result = await aupdate_from_input(post, None, m2m={"tags": [red]})
        assert result.changed_fields == ()
