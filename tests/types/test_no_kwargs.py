"""Tests for the ``NoKwargs`` empty-``TypedDict`` stub."""

from __future__ import annotations

from rest_framework_services import NoKwargs


def test_is_typed_dict_subclass() -> None:
    # ``TypedDict`` types behave as plain dicts at runtime; they have no
    # required keys and accept the empty-mapping literal.
    obj: NoKwargs = {}
    assert obj == {}


def test_total_required_keys_is_empty() -> None:
    # ``TypedDict`` exposes ``__required_keys__`` / ``__optional_keys__``;
    # ``NoKwargs`` should expose no keys at all.
    assert NoKwargs.__required_keys__ == frozenset()
    assert NoKwargs.__optional_keys__ == frozenset()
