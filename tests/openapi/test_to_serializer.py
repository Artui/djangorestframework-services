"""Unit tests for ``openapi._to_serializer.to_serializer_class``."""

from __future__ import annotations

from dataclasses import dataclass

from rest_framework import serializers
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services.openapi._to_serializer import to_serializer_class


@dataclass
class _Foo:
    name: str
    count: int = 0


class _PlainSerializer(serializers.Serializer):
    name = serializers.CharField()


def test_none_returns_none() -> None:
    assert to_serializer_class(None) is None


def test_serializer_subclass_passes_through() -> None:
    assert to_serializer_class(_PlainSerializer) is _PlainSerializer


def test_dataclass_is_wrapped_in_dataclass_serializer() -> None:
    cls = to_serializer_class(_Foo)
    assert cls is not None
    assert issubclass(cls, DataclassSerializer)
    assert cls.Meta.dataclass is _Foo


def test_unrecognised_returns_none() -> None:
    assert to_serializer_class(int) is None
    assert to_serializer_class(object) is None
