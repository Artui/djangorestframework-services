"""Tests for ``enforce_permissions``."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from rest_framework_services.dispatch.build_offline_context import build_offline_context
from rest_framework_services.dispatch.enforce_permissions import enforce_permissions
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


class _Allow(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return True


class _DenyRequest(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return False


class _DenyObject(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return False


class _DenyWithMessage(BasePermission):
    message = "no entry"
    code = "forbidden_custom"

    def has_permission(self, request: Any, view: Any) -> bool:
        return False


def _service(**_kwargs: object) -> None: ...


def _context() -> Any:
    return build_offline_context(object())


def test_none_permission_classes_is_noop() -> None:
    enforce_permissions(ServiceSpec(service=_service), _context())
    enforce_permissions(SelectorSpec(kind=SelectorKind.LIST), _context())


def test_empty_permission_classes_is_noop() -> None:
    enforce_permissions(ServiceSpec(service=_service, permission_classes=[]), _context())


def test_allows_when_all_pass() -> None:
    spec = ServiceSpec(service=_service, permission_classes=[_Allow])
    enforce_permissions(spec, _context())


def test_denies_when_a_permission_fails() -> None:
    spec = ServiceSpec(service=_service, permission_classes=[_DenyRequest])
    with pytest.raises(PermissionDenied):
        enforce_permissions(spec, _context())


def test_carries_permission_message_and_code() -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, permission_classes=[_DenyWithMessage])
    with pytest.raises(PermissionDenied) as exc:
        enforce_permissions(spec, _context())
    assert "no entry" in str(exc.value)
    assert exc.value.get_codes() == "forbidden_custom"


def test_first_failing_permission_in_a_list_denies() -> None:
    spec = ServiceSpec(service=_service, permission_classes=[_Allow, _DenyRequest])
    with pytest.raises(PermissionDenied):
        enforce_permissions(spec, _context())


def test_object_permission_denies_when_instance_supplied() -> None:
    spec = ServiceSpec(service=_service, permission_classes=[_DenyObject])
    with pytest.raises(PermissionDenied):
        enforce_permissions(spec, _context(), instance=object())


def test_object_permission_allows_when_instance_supplied() -> None:
    spec = ServiceSpec(service=_service, permission_classes=[_Allow])
    enforce_permissions(spec, _context(), instance=object())


def test_object_permission_skipped_without_instance() -> None:
    # ``_DenyObject`` would deny object-level, but ``has_object_permission`` is
    # only consulted when an instance is supplied — so this passes.
    spec = ServiceSpec(service=_service, permission_classes=[_DenyObject])
    enforce_permissions(spec, _context())
