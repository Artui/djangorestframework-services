"""Tests for the marking's named constructors."""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from rest_framework_services.types.field_audience import FieldAudience
from rest_framework_services.types.field_marking import FieldMarking
from rest_framework_services.types.value_formatter import ValueFormatter


def test_an_unmarked_default_carries_no_formatter() -> None:
    """Unset is today's behaviour, and every constructor that predates this keeps it."""
    assert FieldMarking().formatter is None
    assert FieldMarking.handle().formatter is None
    assert FieldMarking.hidden().formatter is None
    assert FieldMarking.label().formatter is None


def test_formatted_is_content_carrying_the_transform() -> None:
    formatter = ValueFormatter(str, "string")

    marking = FieldMarking.formatted(formatter, "Spelled out.")

    assert marking.audience is FieldAudience.CONTENT
    assert marking.description == "Spelled out."
    assert marking.formatter is formatter


def test_timestamp_is_a_named_constructor_over_the_same_mechanism() -> None:
    marking = FieldMarking.timestamp()

    assert marking.audience is FieldAudience.CONTENT
    assert marking.formatter is not None
    assert marking.formatter.produces == "string"


def test_a_timestamp_format_reaches_the_formatter_and_its_example() -> None:
    marking = FieldMarking.timestamp("%Y/%m/%d", "Due date.")
    formatter: Any = marking.formatter

    assert marking.description == "Due date."
    with timezone.override("UTC"):
        assert formatter.apply("2026-01-31T12:00:00Z") == "2026/01/31"
    assert formatter.json_schema() == {"type": "string", "examples": ["2026/01/31"]}
