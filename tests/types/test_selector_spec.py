"""Tests for the SelectorSpec dataclass."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Count, Q

from rest_framework_services import Affordance, SelectorKind, SelectorSpec, ServiceSpec
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


def _with_codes(*codes: str) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=_noop,
        affordances=[Affordance(code=code, reason="r", when=Q(pk__isnull=False)) for code in codes],
    )


def _listing(**fields: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(kind=SelectorKind.LIST, selector=_noop, **fields)


class TestSelectorSpecAffordances:
    def test_undeclared_is_none(self) -> None:
        assert SelectorSpec(kind=SelectorKind.LIST).affordances is None

    def test_a_mapping_of_specs_is_stored_as_given(self) -> None:
        declared = {"publish": _with_codes("a"), "archive": ServiceSpec(service=_noop)}
        assert _listing(affordances=declared).affordances is declared

    def test_a_non_mapping_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="must be a mapping of name -> ServiceSpec"):
            _listing(affordances=[_with_codes("a")])

    @pytest.mark.parametrize("key", ["", 7], ids=["empty", "not-a-string"])
    def test_keys_must_be_non_empty_strings(self, key: Any) -> None:
        with pytest.raises(ImproperlyConfigured, match="keys must be non-empty strings"):
            _listing(affordances={key: _with_codes("a")})

    def test_a_registry_name_is_refused_in_place_of_the_spec(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="not its registry name"):
            _listing(affordances={"publish": "publish_post"})

    def test_a_generated_annotation_colliding_with_a_declared_one_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="which `annotations` already declares"):
            _listing(
                annotations={"affordance__publish__a": Count("pk")},
                affordances={"publish": _with_codes("a")},
            )

    def test_an_unrelated_declared_annotation_is_left_alone(self) -> None:
        """Sized so a match on the prefix alone would refuse."""
        _listing(
            annotations={"affordance__publish__b": Count("pk")},
            affordances={"publish": _with_codes("a")},
        )

    def test_two_entries_generating_one_annotation_are_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="both generate the annotation") as info:
            _listing(affordances={"a__b": _with_codes("c"), "a": _with_codes("b__c")})
        # The earlier check has nothing to answer: no ``annotations`` are declared.
        assert "already declares" not in str(info.value)
