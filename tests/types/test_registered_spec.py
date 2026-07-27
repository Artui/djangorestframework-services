"""Tests for the RegisteredSpec dataclass."""

from __future__ import annotations

import pytest

from rest_framework_services import RegisteredSpec, SelectorKind, SelectorSpec, ServiceSpec


def _noop() -> None:
    return None


class TestRegisteredSpec:
    def test_defaults_to_no_tags(self) -> None:
        spec = ServiceSpec(service=_noop)
        entry = RegisteredSpec(name="refund_order", spec=spec)
        assert entry.name == "refund_order"
        assert entry.spec is spec
        assert entry.tags == frozenset()

    def test_carries_tags(self) -> None:
        entry = RegisteredSpec(
            name="list_orders",
            spec=SelectorSpec(kind=SelectorKind.LIST),
            tags=frozenset({"read", "public"}),
        )
        assert entry.tags == {"read", "public"}

    def test_accepts_positional_arguments(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE)
        entry = RegisteredSpec("get_order", spec)
        assert entry.name == "get_order"
        assert entry.spec is spec

    def test_frozen(self) -> None:
        entry = RegisteredSpec(name="refund_order", spec=ServiceSpec(service=_noop))
        with pytest.raises(AttributeError):
            entry.name = "other"  # type: ignore[misc]

    def test_kind_is_not_stored(self) -> None:
        # Derived by isinstance wherever it is needed, so it can never drift
        # from the spec it describes.
        entry = RegisteredSpec(name="list_orders", spec=SelectorSpec(kind=SelectorKind.LIST))
        assert not hasattr(entry, "kind")
