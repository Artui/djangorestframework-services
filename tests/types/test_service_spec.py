"""Tests for the ServiceSpec dataclass."""

from __future__ import annotations

import pytest

from rest_framework_services import ServiceSpec


def _noop() -> None:
    return None


class TestServiceSpec:
    def test_defaults(self) -> None:
        spec = ServiceSpec(service=_noop)
        assert spec.service is _noop
        assert spec.input_serializer is None
        assert spec.output_serializer is None
        assert spec.output_selector is None
        assert spec.atomic is True
        assert spec.success_status is None

    def test_frozen(self) -> None:
        spec = ServiceSpec(service=_noop)
        with pytest.raises(AttributeError):
            spec.atomic = False  # type: ignore[misc]
