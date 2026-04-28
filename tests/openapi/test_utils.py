"""Unit tests for ``openapi.utils.default_status``."""

from __future__ import annotations

from typing import Any

from rest_framework_services import (
    ServiceCreateView,
    ServiceDeleteView,
    ServiceUpdateView,
)
from rest_framework_services.openapi.utils import default_status


def _bound_action(action: str) -> Any:
    class _V:
        pass

    v = _V()
    v.action = action  # ty: ignore[unresolved-attribute]
    return v


def test_create_action_defaults_to_201() -> None:
    assert default_status(_bound_action("create")) == 201


def test_destroy_action_defaults_to_204() -> None:
    assert default_status(_bound_action("destroy")) == 204


def test_partial_update_defaults_to_200() -> None:
    assert default_status(_bound_action("partial_update")) == 200


def test_unknown_action_defaults_to_200() -> None:
    assert default_status(_bound_action("approve")) == 200


def test_view_class_default_for_create() -> None:
    assert default_status(ServiceCreateView()) == 201


def test_view_class_default_for_update() -> None:
    assert default_status(ServiceUpdateView()) == 200


def test_view_class_default_for_delete() -> None:
    assert default_status(ServiceDeleteView()) == 204


def test_unrecognised_view_falls_back_to_200() -> None:
    class _Vanilla:
        pass

    assert default_status(_Vanilla()) == 200
