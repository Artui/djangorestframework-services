"""Tests for the resolved-projection value type."""

from __future__ import annotations

from rest_framework_services.types.audience_projection import AudienceProjection
from rest_framework_services.types.field_audience import FieldAudience
from rest_framework_services.types.field_marking import FieldMarking
from rest_framework_services.types.value_formatter import ValueFormatter


def test_default_projection_is_empty() -> None:
    assert AudienceProjection().is_empty()


def test_a_nested_marking_alone_makes_it_non_empty() -> None:
    """The parent declares nothing, so only the child can answer this."""
    parent = AudienceProjection(
        nested={"lines": AudienceProjection(fields={"sku": FieldMarking.hidden()})}
    )

    assert not parent.is_empty()


def test_an_empty_child_leaves_it_empty() -> None:
    assert AudienceProjection(nested={"lines": AudienceProjection()}).is_empty()


def test_audience_defaults_to_content() -> None:
    projection = AudienceProjection(fields={"etag": FieldMarking.hidden()})

    assert projection.audience("etag") is FieldAudience.HIDDEN
    assert projection.audience("anything-else") is FieldAudience.CONTENT


def test_a_formatter_alone_makes_it_non_empty() -> None:
    """It rides on a marking, so ``fields`` already answers this — no term of its own."""
    projection = AudienceProjection(fields={"due_at": FieldMarking.timestamp()})

    assert not projection.is_empty()


def test_formatter_is_none_for_an_unmarked_or_unformatted_field() -> None:
    projection = AudienceProjection(fields={"etag": FieldMarking.hidden()})

    assert projection.formatter("etag") is None
    assert projection.formatter("anything-else") is None


def test_a_marked_field_offers_its_formatter() -> None:
    marking = FieldMarking.timestamp()

    projection = AudienceProjection(fields={"due_at": marking})

    assert projection.formatter("due_at") is marking.formatter


def test_a_handle_never_formats_however_the_two_were_declared_together() -> None:
    """The suppression lives here so both walks read the same answer."""
    formatter = ValueFormatter(str, "string")
    projection = AudienceProjection(
        fields={"id": FieldMarking(FieldAudience.HANDLE, formatter=formatter)}
    )

    assert projection.fields["id"].formatter is formatter
    assert projection.formatter("id") is None
