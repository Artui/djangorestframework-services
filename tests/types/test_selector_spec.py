"""Tests for the SelectorSpec dataclass."""

from __future__ import annotations

from types import MappingProxyType

import pytest
from django.core.exceptions import ImproperlyConfigured

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


class TestSelectorSpecMetadata:
    def test_defaults_to_none(self) -> None:
        assert SelectorSpec(kind=SelectorKind.LIST).metadata is None

    def test_an_empty_mapping_stays_distinct_from_undeclared(self) -> None:
        assert SelectorSpec(kind=SelectorKind.LIST, metadata={}).metadata == {}

    def test_is_stored_as_given_not_copied(self) -> None:
        declaration = {"scope": "tenant"}
        spec = SelectorSpec(kind=SelectorKind.LIST, metadata=declaration)
        assert spec.metadata is declaration

    def test_accepts_any_mapping(self) -> None:
        declaration = MappingProxyType({"scope": "tenant"})
        spec = SelectorSpec(kind=SelectorKind.LIST, metadata=declaration)
        assert spec.metadata is declaration

    def test_non_mapping_rejected_at_construction(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="SelectorSpec.metadata must be a mapping"):
            SelectorSpec(kind=SelectorKind.LIST, metadata=["scope"])  # type: ignore[arg-type]

    def test_reachable_without_a_registry(self) -> None:
        # The load-bearing property: a permission class holds a view, the view
        # holds the spec, and that is the whole path to the declaration.
        action_specs = {"list": SelectorSpec(kind=SelectorKind.LIST, metadata={"scope": "tenant"})}
        assert action_specs["list"].metadata == {"scope": "tenant"}
