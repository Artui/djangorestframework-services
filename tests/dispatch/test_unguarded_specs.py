"""Tests for ``unguarded_specs``."""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission

from rest_framework_services.dispatch.unguarded_specs import unguarded_specs
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


class _Allow(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return True


def _service(**_: Any) -> None: ...


def test_names_specs_whose_permission_classes_is_none() -> None:
    assert unguarded_specs(
        {
            "open": ServiceSpec(service=_service),
            "guarded": ServiceSpec(service=_service, permission_classes=[_Allow]),
        }
    ) == ["open"]


def test_an_empty_permission_list_counts_as_guarded() -> None:
    # ``[]`` is a deliberate "no permissions required" — the author answered the
    # question. ``None`` is the absence of an answer, and only off HTTP is there
    # nothing left to supply one. Conflating them would make the honest way to
    # declare an open endpoint indistinguishable from forgetting to think about
    # it, and every consumer would learn to pass ``[]`` to silence the check.
    assert (
        unguarded_specs({"open_on_purpose": ServiceSpec(service=_service, permission_classes=[])})
        == []
    )


def test_covers_selectors_too() -> None:
    assert unguarded_specs({"read": SelectorSpec(kind=SelectorKind.LIST)}) == ["read"]


def test_preserves_declaration_order() -> None:
    specs = {"b": ServiceSpec(service=_service), "a": ServiceSpec(service=_service)}
    # Not sorted: a transport builds its error message from this, and listing
    # specs in the order the author declared them is easier to act on.
    assert unguarded_specs(specs) == ["b", "a"]


def test_all_guarded_is_empty() -> None:
    assert unguarded_specs({"g": ServiceSpec(service=_service, permission_classes=[_Allow])}) == []
