"""Tests for the SelectorSpec dataclass."""

from __future__ import annotations

import pytest

from rest_framework_services import SelectorSpec
from tests.testapp.serializers import AuthorSerializer


def _noop() -> None:
    return None


class TestSelectorSpec:
    def test_defaults(self) -> None:
        spec = SelectorSpec()
        assert spec.selector is None
        assert spec.output_serializer is None
        assert spec.permission_classes is None

    def test_with_selector(self) -> None:
        spec = SelectorSpec(selector=_noop)
        assert spec.selector is _noop
        assert spec.output_serializer is None

    def test_with_output_serializer(self) -> None:
        spec = SelectorSpec(output_serializer=AuthorSerializer)
        assert spec.selector is None
        assert spec.output_serializer is AuthorSerializer

    def test_with_both(self) -> None:
        spec = SelectorSpec(selector=_noop, output_serializer=AuthorSerializer)
        assert spec.selector is _noop
        assert spec.output_serializer is AuthorSerializer

    def test_frozen(self) -> None:
        spec = SelectorSpec()
        with pytest.raises(AttributeError):
            spec.selector = _noop  # type: ignore[misc]
