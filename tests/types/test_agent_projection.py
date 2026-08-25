"""Tests for the resolved-projection value type."""

from __future__ import annotations

from rest_framework_services.types.agent_field import AgentField
from rest_framework_services.types.agent_projection import AgentProjection
from rest_framework_services.types.field_audience import FieldAudience


def test_default_projection_is_empty() -> None:
    assert AgentProjection().is_empty()


def test_a_nested_marking_alone_makes_it_non_empty() -> None:
    """The parent declares nothing, so only the child can answer this."""
    parent = AgentProjection(nested={"lines": AgentProjection(fields={"sku": AgentField.hidden()})})

    assert not parent.is_empty()


def test_an_empty_child_leaves_it_empty() -> None:
    assert AgentProjection(nested={"lines": AgentProjection()}).is_empty()


def test_audience_defaults_to_content() -> None:
    projection = AgentProjection(fields={"etag": AgentField.hidden()})

    assert projection.audience("etag") is FieldAudience.HIDDEN
    assert projection.audience("anything-else") is FieldAudience.CONTENT
