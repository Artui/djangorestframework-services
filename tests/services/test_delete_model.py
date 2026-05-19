"""Tests for ``delete_model``."""

from __future__ import annotations

import pytest

from rest_framework_services import delete_model
from tests.testapp.models import Author


@pytest.mark.django_db
class TestModelDeleteService:
    def test_happy_path_deletes_instance(self) -> None:
        author = Author.objects.create(name="Ada")
        pk = author.pk
        service = delete_model(Author)
        result = service(instance=author)
        assert result is None
        assert not Author.objects.filter(pk=pk).exists()

    def test_soft_delete_hook_replaces_delete(self) -> None:
        author = Author.objects.create(name="Ada")
        pk = author.pk
        calls: list[Author] = []

        def _archive(inst: Author) -> None:
            calls.append(inst)

        service = delete_model(Author, soft_delete=_archive)
        service(instance=author)
        assert calls == [author]
        # Hard delete didn't run — author still in the database.
        assert Author.objects.filter(pk=pk).exists()

    def test_returned_callable_absorbs_arbitrary_extras(self) -> None:
        author = Author.objects.create(name="Ada")
        pk = author.pk
        service = delete_model(Author)
        service(
            instance=author,
            request=object(),
            user=object(),
            tenant_id=42,
            anything_else="ignored",
        )
        assert not Author.objects.filter(pk=pk).exists()
