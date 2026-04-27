"""Tests for acreate_from_input."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rest_framework_services.mutations import acreate_from_input
from rest_framework_services.types import UNSET
from rest_framework_services.types.unset import _Unset
from tests.testapp.models import Author, Post, Tag


@dataclass
class _PostInput:
    title: str
    body: str = "default-body"


@pytest.mark.django_db(transaction=True)
class TestACreateFromInput:
    async def test_creates_persisted_instance(self) -> None:
        result = await acreate_from_input(Author, {"name": "Ada"})
        assert result.created is True
        assert result.instance.pk is not None
        assert await Author.objects.acount() == 1

    async def test_changes_record_creation_fields(self) -> None:
        result = await acreate_from_input(Author, {"name": "Ada"})
        assert result.changed_fields == ("name",)
        change = result.get_field_change("name")
        assert change is not None
        assert isinstance(change.old, _Unset)
        assert change.new == "Ada"

    async def test_dataclass_input(self) -> None:
        result = await acreate_from_input(Post, _PostInput(title="hi"))
        assert result.instance.title == "hi"
        assert result.instance.body == "default-body"

    async def test_unset_skipped(self) -> None:
        @dataclass
        class _Sparse:
            title: str = "abc"
            body: object = UNSET

        result = await acreate_from_input(Post, _Sparse())
        assert result.changed_fields == ("title",)

    async def test_field_map(self) -> None:
        result = await acreate_from_input(
            Post,
            {"headline": "Mapped"},
            field_map={"headline": "title"},
        )
        assert result.instance.title == "Mapped"

    async def test_exclude_fields(self) -> None:
        result = await acreate_from_input(
            Author,
            {"name": "x", "drop": "y"},
            exclude_fields=["drop"],
        )
        assert result.instance.name == "x"

    async def test_m2m_assignment(self) -> None:
        red = await Tag.objects.acreate(name="red")
        blue = await Tag.objects.acreate(name="blue")
        result = await acreate_from_input(
            Post,
            {"title": "tagged"},
            m2m={"tags": [red, blue]},
        )
        pks = {pk async for pk in result.instance.tags.values_list("pk", flat=True)}
        assert pks == {red.pk, blue.pk}
        assert "tags" in result.changed_fields

    async def test_m2m_none_treated_as_empty(self) -> None:
        result = await acreate_from_input(
            Post,
            {"title": "no-tags"},
            m2m={"tags": None},
        )
        pks = [pk async for pk in result.instance.tags.values_list("pk", flat=True)]
        assert pks == []
