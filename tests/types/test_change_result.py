"""Tests for the ChangeResult dataclass."""

from __future__ import annotations

import pytest

from rest_framework_services.types.change_result import ChangeResult
from rest_framework_services.types.field_change import FieldChange
from tests.testapp.models import Author


@pytest.mark.django_db
class TestChangeResult:
    def test_changed_fields_property(self) -> None:
        author = Author(name="x")
        result = ChangeResult(
            instance=author,
            created=True,
            changes=(
                FieldChange(field="name", old=None, new="x"),
                FieldChange(field="age", old=None, new=33),
            ),
        )
        assert result.changed_fields == ("name", "age")

    def test_get_field_change_hit(self) -> None:
        author = Author(name="x")
        change = FieldChange(field="name", old=None, new="x")
        result = ChangeResult(instance=author, created=True, changes=(change,))
        assert result.get_field_change("name") is change

    def test_get_field_change_miss(self) -> None:
        author = Author(name="x")
        result = ChangeResult(instance=author, created=True, changes=())
        assert result.get_field_change("name") is None

    def test_truthy_when_any_change(self) -> None:
        author = Author(name="x")
        result = ChangeResult(
            instance=author,
            created=False,
            changes=(FieldChange(field="name", old="a", new="b"),),
        )
        assert bool(result) is True

    def test_falsy_when_no_changes(self) -> None:
        author = Author(name="x")
        result = ChangeResult(instance=author, created=False, changes=())
        assert bool(result) is False
