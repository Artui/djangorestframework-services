"""Tests for the internal ``resolve_m2m`` helper used by the model service factories."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.services._resolve_m2m import resolve_m2m


def test_none_returns_none() -> None:
    assert resolve_m2m(None, object()) is None


def test_static_mapping_round_trips() -> None:
    result = resolve_m2m({"tags": [1, 2]}, object())
    assert result == {"tags": [1, 2]}


def test_callable_resolves_from_data() -> None:
    def _from_data(data: Any) -> Mapping[str, Any]:
        return {"tags": data}

    result = resolve_m2m(_from_data, [1, 2, 3])
    assert result == {"tags": [1, 2, 3]}


def test_returns_a_new_dict_not_the_input_mapping() -> None:
    """Ensures callers (the mutation helpers) get a mutable owned copy."""
    src = {"tags": [1]}
    result = resolve_m2m(src, object())
    assert result is not None
    assert result is not src
