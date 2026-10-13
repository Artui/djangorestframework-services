"""Tests for ActionSerializerResolver."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import serializers
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    ActionSerializerResolver,
    PolymorphicServiceSpec,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)
from rest_framework_services.viewsets.action_serializer_resolver import _body_serializer
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


def _variant_a(*, data: Any) -> str:  # pragma: no cover - never called
    raise AssertionError


class _OtherIn(serializers.Serializer):
    other = serializers.CharField()


def test_disagreeing_variants_name_no_body_when_asked_directly() -> None:
    """``check_permissions`` leaves these out of OPTIONS before a body is
    asked for, so only a direct question reaches this answer: one variant's
    body is never handed back as if it were the only one."""
    spec = PolymorphicServiceSpec(
        discriminator=_variant_a,
        specs={
            "a": ServiceSpec(service=_noop, input_serializer=AuthorSerializer),
            "b": ServiceSpec(service=_noop, input_serializer=_OtherIn),
        },
    )
    assert _body_serializer(spec) is None
