"""Tests for the ServiceSpec dataclass."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

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
