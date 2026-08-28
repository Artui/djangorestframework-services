"""Tests for the resolved-projection value type."""

from __future__ import annotations

from rest_framework_services.types.audience_projection import AudienceProjection
from rest_framework_services.types.field_audience import FieldAudience
from rest_framework_services.types.field_marking import FieldMarking


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
