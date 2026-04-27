"""Tests for MultiSerializerMixin in isolation."""

from __future__ import annotations

import pytest
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import MultiSerializerMixin
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


class _Vs(MultiSerializerMixin, GenericViewSet):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer
    serializer_classes = {"list": AuthorSerializer}


def test_returns_mapped_class_for_known_action() -> None:
    vs = _Vs()
    vs.action = "list"
    assert vs.get_serializer_class() is AuthorSerializer


def test_falls_back_to_serializer_class_for_unmapped_action() -> None:
    vs = _Vs()
    vs.action = "create"
    assert vs.get_serializer_class() is AuthorSerializer


def test_action_none_falls_back_to_super() -> None:
    """When ``self.action`` is ``None`` (e.g. accessed outside an action
    dispatch), the mapping is skipped entirely."""
    vs = _Vs()
    vs.action = None
    assert vs.get_serializer_class() is AuthorSerializer


def test_super_raises_when_no_serializer_configured() -> None:
    class _NoSerializer(MultiSerializerMixin, GenericViewSet):
        queryset = Author.objects.all()

    vs = _NoSerializer()
    vs.action = None
    with pytest.raises(AssertionError):
        vs.get_serializer_class()
