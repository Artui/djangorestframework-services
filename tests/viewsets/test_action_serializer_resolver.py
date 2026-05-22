"""Tests for ActionSerializerResolver."""

from __future__ import annotations

import pytest
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    ActionSerializerResolver,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


def _noop() -> None: ...


class _Vs(ActionSerializerResolver, GenericViewSet):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer
    action_specs = {
        "list": SelectorSpec(kind=SelectorKind.LIST, output_serializer=AuthorSerializer),
        "create": ServiceSpec(
            service=_noop,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
            ),
        ),
        "retrieve": SelectorSpec(kind=SelectorKind.RETRIEVE),
    }


class TestActionSerializerResolver:
    def test_selector_spec_returns_output_serializer(self) -> None:
        vs = _Vs()
        vs.action = "list"
        assert vs.get_serializer_class() is AuthorSerializer

    def test_service_spec_returns_output_serializer(self) -> None:
        vs = _Vs()
        vs.action = "create"
        assert vs.get_serializer_class() is AuthorSerializer

    def test_selector_spec_with_no_output_serializer_falls_back(self) -> None:
        vs = _Vs()
        vs.action = "retrieve"
        assert vs.get_serializer_class() is AuthorSerializer

    def test_action_not_in_map_falls_back_to_serializer_class(self) -> None:
        vs = _Vs()
        vs.action = "update"
        assert vs.get_serializer_class() is AuthorSerializer

    def test_action_none_falls_back_to_serializer_class(self) -> None:
        vs = _Vs()
        vs.action = None
        assert vs.get_serializer_class() is AuthorSerializer

    def test_no_serializer_configured_raises(self) -> None:
        class _NoSerializer(ActionSerializerResolver, GenericViewSet):
            queryset = Author.objects.all()

        vs = _NoSerializer()
        vs.action = None
        with pytest.raises(AssertionError):
            vs.get_serializer_class()
