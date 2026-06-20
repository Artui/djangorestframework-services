"""Unit tests for ``ChildSpec`` construction + validation."""

from __future__ import annotations

import pytest

from rest_framework_services import ChildSpec
from tests.testapp.models import Section


class TestChildSpec:
    def test_defaults(self) -> None:
        spec = ChildSpec(model=Section, fk="catalog")
        assert spec.match_key == "pk"
        assert spec.mode == "replace"
        assert spec.m2m is None
        assert spec.children is None

    def test_merge_mode_allowed(self) -> None:
        assert ChildSpec(model=Section, fk="catalog", mode="merge").mode == "merge"

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="ChildSpec.mode must be one of"):
            ChildSpec(model=Section, fk="catalog", mode="upsert")
