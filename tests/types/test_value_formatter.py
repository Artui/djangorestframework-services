"""Tests for the declared value transform and the schema it advertises."""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.utils import timezone

from rest_framework_services.types.value_formatter import ValueFormatter

UTC_NOON = "2026-01-31T12:00:00Z"


def _shout(value: Any) -> Any:
    return str(value).upper()


def test_a_formatter_writes_its_declared_type_into_the_schema() -> None:
    formatter = ValueFormatter(_shout, "string", {"examples": ["LOUD"]})

    assert formatter.json_schema() == {"type": "string", "examples": ["LOUD"]}


def test_the_fragment_merges_over_the_written_type() -> None:
    """Everything about the *shape* of the produced value comes from the fragment."""
    formatter = ValueFormatter(_shout, "string", {"format": "email", "description": "Shouted."})

    assert formatter.json_schema() == {
        "type": "string",
        "format": "email",
        "description": "Shouted.",
    }


def test_a_formatter_with_no_fragment_declares_only_its_type() -> None:
    assert ValueFormatter(_shout, "number").json_schema() == {"type": "number"}


def test_the_fragment_may_not_declare_the_type() -> None:
    """The honesty guarantee: a renderer cannot contradict its own advertisement."""
    with pytest.raises(ImproperlyConfigured) as caught:
        ValueFormatter(_shout, "string", {"type": "integer"})

    assert "may not set 'type'" in str(caught.value)
    assert "produces='integer'" in str(caught.value)


def test_produces_must_name_a_json_type() -> None:
    # A type checker refuses this at the call site; the runtime check is for the
    # consumer who has none, and for a name assembled rather than written out.
    with pytest.raises(ImproperlyConfigured) as caught:
        ValueFormatter(_shout, "str")

    assert "is not a JSON type" in str(caught.value)


def test_a_null_never_reaches_the_transform() -> None:
    def explode(value: Any) -> Any:
        raise AssertionError(f"called with {value!r}")

    assert ValueFormatter(explode, "string").apply(None) is None


def test_apply_runs_the_transform_for_anything_else() -> None:
    assert ValueFormatter(_shout, "string").apply("quiet") == "QUIET"


def test_a_timestamp_renders_in_the_active_timezone() -> None:
    """The zone is read from Django, which is what makes both transports agree."""
    formatter = ValueFormatter.timestamp("%Y-%m-%d %H:%M")

    with timezone.override("UTC"):
        in_utc = formatter.apply(UTC_NOON)
    with timezone.override("Australia/Sydney"):
        in_sydney = formatter.apply(UTC_NOON)

    # The same instant, and the only thing that moved is the active zone.
    assert in_utc == "2026-01-31 12:00"
    assert in_sydney == "2026-01-31 23:00"


@override_settings(TIME_ZONE="Europe/Warsaw")
def test_a_timestamp_follows_the_projects_own_timezone_setting() -> None:
    """No zone is threaded in, so the settings default is what applies."""
    assert ValueFormatter.timestamp("%H:%M").apply(UTC_NOON) == "13:00"


def test_a_timestamp_accepts_the_datetime_a_field_may_hand_over() -> None:
    """``DateTimeField(format=None)`` renders the object rather than a string."""
    aware = datetime.datetime(2026, 1, 31, 12, 0, tzinfo=datetime.timezone.utc)

    with timezone.override("UTC"):
        assert ValueFormatter.timestamp("%H:%M").apply(aware) == "12:00"


def test_a_naive_datetime_is_formatted_where_it_stands() -> None:
    """The ``USE_TZ = False`` project: there is no zone to convert from."""
    naive = datetime.datetime(2026, 1, 31, 12, 0)

    with timezone.override("Australia/Sydney"):
        assert ValueFormatter.timestamp("%H:%M").apply(naive) == "12:00"


@pytest.mark.parametrize("value", ["not a date", 7], ids=["prose", "number"])
def test_a_value_that_is_not_a_datetime_passes_through(value: Any) -> None:
    """Same rule as an unrecognised choice constant: report it, do not crash."""
    assert ValueFormatter.timestamp().apply(value) == value


def test_a_bare_date_is_read_as_midnight() -> None:
    """So a ``DateField`` can be formatted too, given a format without a time.

    Deterministic on every supported combination: Django has tried
    ``datetime.fromisoformat`` first since 4.1, and that has accepted a bare
    date since Python 3.7.
    """
    assert ValueFormatter.timestamp("%d %b %Y").apply("2026-01-31") == "31 Jan 2026"
    assert ValueFormatter.timestamp().apply("2026-01-31") == "31 Jan 2026 00:00"


def test_the_timestamp_example_is_rendered_from_the_format() -> None:
    """So the advertised shape cannot drift from what the field carries."""
    schema = ValueFormatter.timestamp("%m/%d/%y %I:%M %p").json_schema()

    assert schema == {"type": "string", "examples": ["01/31/26 02:05 PM"]}


def test_the_default_timestamp_format_is_day_first_and_24_hour() -> None:
    assert ValueFormatter.timestamp().json_schema()["examples"] == ["31 Jan 2026 14:05"]
    assert ValueFormatter.timestamp(None).json_schema()["examples"] == ["31 Jan 2026 14:05"]
