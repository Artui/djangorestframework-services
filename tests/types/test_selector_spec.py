"""Tests for the SelectorSpec dataclass."""

from __future__ import annotations

import pytest

from rest_framework_services import SelectorKind, SelectorSpec
from tests.testapp.serializers import AuthorSerializer


def _noop() -> None:
    return None


class TestSelectorSpec:
    def test_defaults(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST)
        assert spec.kind is SelectorKind.LIST
        assert spec.selector is None
        assert spec.output_serializer is None
        assert spec.permission_classes is None

    def test_with_selector(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_noop)
        assert spec.kind is SelectorKind.RETRIEVE
        assert spec.selector is _noop
        assert spec.output_serializer is None

    def test_with_output_serializer(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST, output_serializer=AuthorSerializer)
        assert spec.selector is None
        assert spec.output_serializer is AuthorSerializer

    def test_with_both(self) -> None:
        spec = SelectorSpec(
            kind=SelectorKind.LIST, selector=_noop, output_serializer=AuthorSerializer
        )
        assert spec.selector is _noop
        assert spec.output_serializer is AuthorSerializer

    def test_kind_is_required(self) -> None:
        with pytest.raises(TypeError):
            SelectorSpec()  # type: ignore[call-arg]

    def test_kw_only(self) -> None:
        with pytest.raises(TypeError):
            SelectorSpec(SelectorKind.LIST, _noop)  # type: ignore[misc]

    def test_frozen(self) -> None:
        spec = SelectorSpec(kind=SelectorKind.LIST)
        with pytest.raises(AttributeError):
            spec.selector = _noop  # type: ignore[misc]
