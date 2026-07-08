"""Unit tests for ``openapi._resolve`` — the service/selector spec resolvers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from rest_framework_services import (
    SelectorKind,
    SelectorListView,
    SelectorRetrieveView,
    SelectorSpec,
    ServiceCreateView,
    ServiceSpec,
    ServiceViewSet,
    selector_action,
    service_action,
)
from rest_framework_services.openapi._resolve import (
    resolve_selector_spec,
    resolve_service_spec,
)
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


@dataclass
class _AuthorIn:
    name: str


def _create(*, data: _AuthorIn) -> dict[str, Any]:
    return {"name": data.name}


def _list_authors() -> Any:
    return Author.objects.all().order_by("id")


_create_spec = ServiceSpec(
    service=_create,
    input_serializer=_AuthorIn,
    output_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE, output_serializer=AuthorSerializer
    ),
)

_list_spec = SelectorSpec(
    kind=SelectorKind.LIST, selector=_list_authors, output_serializer=AuthorSerializer
)
_retrieve_spec = SelectorSpec(
    kind=SelectorKind.RETRIEVE, selector=_list_authors, output_serializer=AuthorSerializer
)


class _StandaloneView(ServiceCreateView):
    spec = _create_spec


class _ViewSet(ServiceViewSet):
    action_specs = {
        "create": _create_spec,
        "list": _list_spec,
    }


class _ActionViewSet(GenericViewSet):
    @service_action(_create_spec, detail=False, methods=["post"])
    def go(self, request):  # type: ignore[no-untyped-def]
        ...


class _StandaloneListView(SelectorListView):
    queryset = Author.objects.all()
    spec = _list_spec


class _StandaloneRetrieveView(SelectorRetrieveView):
    queryset = Author.objects.all()
    spec = _retrieve_spec


class _SelectorActionViewSet(GenericViewSet):
    queryset = Author.objects.all()

    @selector_action(_list_spec, detail=False)
    def recent(self, request):  # type: ignore[no-untyped-def]
        ...


_factory = APIRequestFactory()


def _bound(view_cls: type, action: str) -> Any:
    """Return a view instance with ``action`` set, like DRF would do."""
    instance = view_cls()
    instance.action = action  # ty: ignore[unresolved-attribute]
    return instance


class TestResolveServiceSpec:
    def test_returns_spec_on_standalone_view(self) -> None:
        view = _StandaloneView()
        assert resolve_service_spec(view) is _create_spec

    def test_returns_spec_on_viewset_action(self) -> None:
        assert resolve_service_spec(_bound(_ViewSet, "create")) is _create_spec

    def test_returns_none_for_selector_action(self) -> None:
        # ``action_specs["list"]`` is a SelectorSpec, not a ServiceSpec.
        assert resolve_service_spec(_bound(_ViewSet, "list")) is None

    def test_returns_spec_on_service_action_handler(self) -> None:
        assert resolve_service_spec(_bound(_ActionViewSet, "go")) is _create_spec

    def test_returns_none_when_no_spec(self) -> None:
        class _Bare:
            spec = None
            action = None

        assert resolve_service_spec(_Bare()) is None

    def test_returns_none_for_unconfigured_action(self) -> None:
        class _Empty(ServiceViewSet):
            action_specs = {}

        assert resolve_service_spec(_bound(_Empty, "create")) is None

    def test_returns_none_when_view_has_no_action_specs(self) -> None:
        # Plain ``GenericViewSet`` with an action set but no spec sources.
        class _Bare(GenericViewSet): ...

        assert resolve_service_spec(_bound(_Bare, "list")) is None

    def test_partial_update_falls_back_to_update_key(self) -> None:
        update_spec = ServiceSpec(service=_create)

        class _View(ServiceViewSet):
            action_specs = {"update": update_spec}

        assert resolve_service_spec(_bound(_View, "partial_update")) is update_spec

    def test_dedicated_partial_update_key_wins(self) -> None:
        update_spec = ServiceSpec(service=_create)
        patch_spec = ServiceSpec(service=_create)

        class _View(ServiceViewSet):
            action_specs = {"update": update_spec, "partial_update": patch_spec}

        assert resolve_service_spec(_bound(_View, "partial_update")) is patch_spec


class TestResolveSelectorSpec:
    def test_returns_spec_on_standalone_list_view(self) -> None:
        assert resolve_selector_spec(_StandaloneListView()) is _list_spec

    def test_returns_spec_on_standalone_retrieve_view(self) -> None:
        assert resolve_selector_spec(_StandaloneRetrieveView()) is _retrieve_spec

    def test_returns_spec_on_viewset_list_action(self) -> None:
        assert resolve_selector_spec(_bound(_ViewSet, "list")) is _list_spec

    def test_returns_none_for_service_action(self) -> None:
        # ``action_specs["create"]`` is a ServiceSpec — resolved non-raising to
        # ``None`` (the schema generator inspects write actions too).
        assert resolve_selector_spec(_bound(_ViewSet, "create")) is None

    def test_returns_spec_on_selector_action_handler(self) -> None:
        assert resolve_selector_spec(_bound(_SelectorActionViewSet, "recent")) is _list_spec

    def test_returns_none_when_no_spec(self) -> None:
        class _Bare:
            spec = None
            action = None

        assert resolve_selector_spec(_Bare()) is None

    def test_returns_none_when_view_has_no_action_specs(self) -> None:
        class _Bare(GenericViewSet): ...

        assert resolve_selector_spec(_bound(_Bare, "list")) is None

    def test_returns_none_for_unconfigured_action(self) -> None:
        class _Empty(ServiceViewSet):
            action_specs = {}

        assert resolve_selector_spec(_bound(_Empty, "list")) is None
