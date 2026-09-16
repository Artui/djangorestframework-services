"""Tests for the ServiceSpec dataclass."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q

from rest_framework_services import Affordance, SelectorKind, SelectorSpec, ServiceSpec
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


class TestServiceSpecMetadata:
    def test_defaults_to_none(self) -> None:
        assert ServiceSpec(service=_noop).metadata is None

    def test_is_stored_as_given_not_copied(self) -> None:
        declaration = {"scope": "tenant"}
        assert ServiceSpec(service=_noop, metadata=declaration).metadata is declaration

    def test_non_mapping_rejected_at_construction(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="ServiceSpec.metadata must be a mapping"):
            ServiceSpec(service=_noop, metadata="tenant")  # type: ignore[arg-type]

    def test_does_not_merge_with_the_nested_output_selector_spec(self) -> None:
        out = SelectorSpec(kind=SelectorKind.RETRIEVE, metadata={"scope": "nested"})
        spec = ServiceSpec(service=_noop, output_selector_spec=out, metadata={"scope": "outer"})
        assert spec.metadata == {"scope": "outer"}
        assert spec.output_selector_spec is not None
        assert spec.output_selector_spec.metadata == {"scope": "nested"}

    def test_a_nested_spec_may_declare_metadata_while_the_parent_does_not(self) -> None:
        out = SelectorSpec(kind=SelectorKind.RETRIEVE, metadata={"scope": "nested"})
        spec = ServiceSpec(service=_noop, output_selector_spec=out)
        assert spec.metadata is None


class TestServiceSpecIdempotent:
    """The declaration is carried verbatim and never defaulted to a claim."""

    def test_defaults_to_undeclared(self) -> None:
        # ``None`` is "nothing said", which a transport must be able to tell
        # apart from a declared ``False`` before it stamps an annotation.
        assert ServiceSpec(service=_noop).idempotent is None

    def test_a_declaration_is_carried_verbatim(self) -> None:
        assert ServiceSpec(service=_noop, idempotent=True).idempotent is True
        assert ServiceSpec(service=_noop, idempotent=False).idempotent is False

    def test_does_not_inherit_from_the_nested_output_selector_spec(self) -> None:
        # ``SelectorSpec`` has no such field: a read is idempotent by
        # construction, so the signal would say nothing there.
        out = SelectorSpec(kind=SelectorKind.RETRIEVE)
        spec = ServiceSpec(service=_noop, output_selector_spec=out, idempotent=True)
        assert spec.idempotent is True
        assert not hasattr(out, "idempotent")


def _row(code: str = "shipped") -> Affordance:
    return Affordance(code=code, reason="Shipped.", when=~Q(published=True))


def _ambient(code: str = "books_closed") -> Affordance:
    return Affordance(code=code, reason="The books are closed.", when=lambda: True)


class TestAffordances:
    def test_undeclared_is_none(self) -> None:
        assert ServiceSpec(service=_noop).affordances is None

    def test_a_sequence_is_stored_as_given(self) -> None:
        declared = (_row(), _ambient())
        assert ServiceSpec(service=_noop, affordances=declared).affordances is declared

    def test_a_single_affordance_is_refused_with_the_fix(self) -> None:
        with pytest.raises(ImproperlyConfigured, match=r"got Affordance\. Wrap a single one"):
            ServiceSpec(service=_noop, affordances=_row())  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "declared",
        [{_row()}, (a for a in [_row()])],
        ids=["set", "generator"],
    )
    def test_a_non_sequence_is_refused(self, declared: Any) -> None:
        """A set has no order to check in, and a generator is spent by the first call."""
        with pytest.raises(ImproperlyConfigured, match="takes a sequence of Affordance"):
            ServiceSpec(service=_noop, affordances=declared)

    def test_a_non_affordance_element_is_named_by_index(self) -> None:
        with pytest.raises(ImproperlyConfigured, match=r"affordances\[1\] must be an Affordance"):
            ServiceSpec(service=_noop, affordances=[_row(), "shipped"])  # type: ignore[list-item]

    def test_a_duplicate_code_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="'shipped' twice"):
            ServiceSpec(service=_noop, affordances=[_row("shipped"), _ambient("shipped")])

    def test_distinct_codes_pass(self) -> None:
        ServiceSpec(service=_noop, affordances=[_row("a"), _row("b")])

    def test_a_row_condition_is_refused_on_a_list_payload_bulk(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="operates on a set"):
            ServiceSpec(service=_noop, many=True, affordances=[_row()])

    def test_a_row_condition_is_refused_on_a_collection_target(self) -> None:
        collection = SelectorSpec(kind=SelectorKind.LIST, selector=_noop)
        with pytest.raises(ImproperlyConfigured, match="operates on a set"):
            ServiceSpec(service=_noop, collection_selector_spec=collection, affordances=[_row()])

    def test_a_callable_condition_is_allowed_on_either_bulk_shape(self) -> None:
        """The refusal is about the row, not about bulk: sized so only that conjunct can
        decide it."""
        collection = SelectorSpec(kind=SelectorKind.LIST, selector=_noop)
        ServiceSpec(service=_noop, many=True, affordances=[_ambient()])
        ServiceSpec(service=_noop, collection_selector_spec=collection, affordances=[_ambient()])

    def test_the_check_reruns_on_replace(self) -> None:
        spec = ServiceSpec(service=_noop, affordances=[_row()])
        with pytest.raises(ImproperlyConfigured, match="operates on a set"):
            replace(spec, many=True)
