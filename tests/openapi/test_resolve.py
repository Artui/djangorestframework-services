"""Unit tests for ``openapi._resolve.resolve_spec``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    SelectorSpec,
    ServiceCreateView,
    ServiceSpec,
    ServiceViewSet,
    service_action,
)
from rest_framework_services.openapi._resolve import resolve_spec
from tests.testapp.serializers import AuthorSerializer


@dataclass
class _AuthorIn:
    name: str


def _create(*, data: _AuthorIn) -> dict[str, Any]:
    return {"name": data.name}


def _approve(*, instance: Any) -> dict[str, Any]:
    return {"approved": True}


_create_spec = ServiceSpec(
    service=_create, input_serializer=_AuthorIn, output_serializer=AuthorSerializer
)


class _StandaloneView(ServiceCreateView):
    spec = _create_spec


class _ViewSet(ServiceViewSet):
    action_specs = {
        "create": _create_spec,
        "list": SelectorSpec(output_serializer=AuthorSerializer),
    }


class _ActionViewSet(GenericViewSet):
    @service_action(_create_spec, detail=False, methods=["post"])
    def go(self, request):  # type: ignore[no-untyped-def]
        pass


_factory = APIRequestFactory()


def _bound(view_cls: type, action: str) -> Any:
    """Return a view instance with ``action`` set, like DRF would do."""
    instance = view_cls()
    instance.action = action  # ty: ignore[unresolved-attribute]
    return instance


class TestResolveSpec:
    def test_returns_spec_on_standalone_view(self) -> None:
        view = _StandaloneView()
        assert resolve_spec(view) is _create_spec

    def test_returns_spec_on_viewset_action(self) -> None:
        assert resolve_spec(_bound(_ViewSet, "create")) is _create_spec

    def test_returns_none_for_selector_action(self) -> None:
        # ``action_specs["list"]`` is a SelectorSpec, not a ServiceSpec.
        assert resolve_spec(_bound(_ViewSet, "list")) is None

    def test_returns_spec_on_service_action_handler(self) -> None:
        assert resolve_spec(_bound(_ActionViewSet, "go")) is _create_spec

    def test_returns_none_when_no_spec(self) -> None:
        class _Bare:
            spec = None
            action = None

        assert resolve_spec(_Bare()) is None

    def test_returns_none_for_unconfigured_action(self) -> None:
        class _Empty(ServiceViewSet):
            action_specs = {}

        assert resolve_spec(_bound(_Empty, "create")) is None

    def test_returns_none_when_view_has_no_action_specs(self) -> None:
        # Plain ``GenericViewSet`` with an action set but no spec sources.
        class _Bare(GenericViewSet):
            pass

        assert resolve_spec(_bound(_Bare, "list")) is None
