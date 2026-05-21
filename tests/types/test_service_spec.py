"""Tests for the ServiceSpec dataclass."""

from __future__ import annotations

import pytest

from rest_framework_services import SelectorKind, SelectorSpec, ServiceSpec
from tests.testapp.serializers import AuthorSerializer


def _noop() -> None:
    return None


class TestServiceSpec:
    def test_defaults(self) -> None:
        spec = ServiceSpec(service=_noop)
        assert spec.service is _noop
        assert spec.input_serializer is None
        assert spec.output_selector_spec is None
        assert spec.atomic is True
        assert spec.success_status is None
        assert spec.permission_classes is None

    def test_with_output_selector_spec(self) -> None:
        out = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_noop, output_serializer=AuthorSerializer
        )
        spec = ServiceSpec(service=_noop, output_selector_spec=out)
        assert spec.output_selector_spec is out
        assert spec.output_selector_spec.selector is _noop
        assert spec.output_selector_spec.output_serializer is AuthorSerializer

    def test_frozen(self) -> None:
        spec = ServiceSpec(service=_noop)
        with pytest.raises(AttributeError):
            spec.atomic = False  # type: ignore[misc]
