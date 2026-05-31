"""Tests for the UNSET sentinel."""

from __future__ import annotations

import copy

from rest_framework_services.types.unset import UNSET, UnsetType


def test_unset_is_singleton() -> None:
    assert UnsetType() is UNSET
    assert UnsetType() is UnsetType()


def test_unset_repr() -> None:
    assert repr(UNSET) == "UNSET"


def test_unset_is_falsy() -> None:
    assert bool(UNSET) is False
    assert not UNSET


def test_unset_copy_returns_same_instance() -> None:
    assert copy.copy(UNSET) is UNSET


def test_unset_deepcopy_returns_same_instance() -> None:
    assert copy.deepcopy(UNSET) is UNSET


def test_unset_only_equal_to_itself() -> None:
    assert UNSET == UNSET  # noqa: PLR0124
    assert UNSET != None  # noqa: E711
    assert UNSET != 0
    assert UNSET != ""
