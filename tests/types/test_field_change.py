"""Tests for the FieldChange dataclass."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from rest_framework_services.types.field_change import FieldChange
from rest_framework_services.types.unset import UNSET


def test_construction_and_attributes() -> None:
    change = FieldChange(field="name", old="a", new="b")
    assert change.field == "name"
    assert change.old == "a"
    assert change.new == "b"


def test_frozen() -> None:
    change = FieldChange(field="x", old=1, new=2)
    with pytest.raises(FrozenInstanceError):
        change.field = "y"  # type: ignore[misc]


def test_old_can_be_unset() -> None:
    change = FieldChange(field="x", old=UNSET, new=5)
    assert change.old is UNSET


def test_equality() -> None:
    a = FieldChange(field="x", old=1, new=2)
    b = FieldChange(field="x", old=1, new=2)
    c = FieldChange(field="x", old=1, new=3)
    assert a == b
    assert a != c
