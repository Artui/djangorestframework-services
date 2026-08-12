"""Unit tests for the singular-relation change carrier."""

from __future__ import annotations

from rest_framework_services import RelatedObjectChange


class TestRelatedObjectChange:
    def test_defaults_to_untouched(self) -> None:
        change = RelatedObjectChange(relation="author")
        assert change.outcome == "untouched"
        assert change.pk is None

    def test_untouched_is_falsy_and_anything_else_is_truthy(self) -> None:
        assert not RelatedObjectChange(relation="author")
        assert RelatedObjectChange(relation="author", outcome="created", pk=1)
        # "cleared" touched no row, but it did change the parent's column.
        assert RelatedObjectChange(relation="author", outcome="cleared")
