"""Tests for ``enforce_permissions``."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, IsAdminUser

from rest_framework_services.dispatch.build_offline_context import build_offline_context
from rest_framework_services.dispatch.enforce_permissions import enforce_permissions
from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from tests.testapp.models import Tag


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


def test_object_permission_denies_when_model_instance_supplied() -> None:
    spec = ServiceSpec(service=_service, permission_classes=[_DenyObject])
    with pytest.raises(PermissionDenied):
        enforce_permissions(spec, _context(), instance=Tag())


def test_object_permission_allows_when_model_instance_supplied() -> None:
    spec = ServiceSpec(service=_service, permission_classes=[_Allow])
    enforce_permissions(spec, _context(), instance=Tag())


def test_object_permission_skipped_without_instance() -> None:
    # ``_DenyObject`` would deny object-level, but ``has_object_permission`` is
    # only consulted when an instance is supplied — so this passes.
    spec = ServiceSpec(service=_service, permission_classes=[_DenyObject])
    enforce_permissions(spec, _context())


def test_object_permission_skipped_for_queryset_instance() -> None:
    # A collection target (the bulk / LIST queryset) is not a Model, so only the
    # class-level check runs: ``_DenyObject`` allows ``has_permission`` and
    # ``has_object_permission`` is never called on the queryset. This is the
    # per-set authorization the BULK decision specifies — no AttributeError, no
    # per-row check.
    spec = SelectorSpec(kind=SelectorKind.LIST, permission_classes=[_DenyObject])
    enforce_permissions(spec, _context(), instance=Tag.objects.all())


def test_class_level_check_still_denies_a_queryset_instance() -> None:
    # Class-level ``has_permission`` still runs for a collection target.
    spec = SelectorSpec(kind=SelectorKind.LIST, permission_classes=[_DenyRequest])
    with pytest.raises(PermissionDenied):
        enforce_permissions(spec, _context(), instance=Tag.objects.all())


def test_object_permission_skipped_for_non_model_instance() -> None:
    # Any non-``Model`` target (e.g. a selector returning a computed object)
    # runs class-level only — object permissions are a per-row Model concept.
    spec = ServiceSpec(service=_service, permission_classes=[_DenyObject])
    enforce_permissions(spec, _context(), instance=object())


def test_a_context_with_no_request_is_refused_by_name() -> None:
    """The documented canonical wiring used to fail as an ``AttributeError``.

    ``TargetGuard`` names ``on_target_resolved=enforce_permissions`` as the
    canonical wiring and ``dispatch_spec`` documents a pure non-HTTP caller as
    passing neither ``request`` nor ``view`` — so the two together produced
    ``'NoneType' object has no attribute 'user'`` from inside DRF, which reads
    like a bug in the caller's permission class rather than a missing argument.
    This docstring's own contract is that the context comes from
    ``build_offline_context``; the refusal now says so.
    """
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=lambda **_: None,
        permission_classes=[IsAdminUser],
    )
    with pytest.raises(ImproperlyConfigured, match="build_offline_context"):
        enforce_permissions(spec, OfflineContext(user="u", request=None, view=None))


def test_a_permission_class_that_ignores_the_request_still_needs_none() -> None:
    """The refusal is a translation, not a gate — object-level-only rules are a
    common shape and worked against a request-less context before this change."""

    class _ObjectOnly(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            return True

    spec = SelectorSpec(
        kind=SelectorKind.LIST, selector=lambda **_: None, permission_classes=[_ObjectOnly]
    )
    enforce_permissions(spec, OfflineContext(user="u", request=None, view=None))


def test_an_attribute_error_with_a_real_request_is_the_callers_own() -> None:
    """Only the ambiguous case is translated; a genuine bug keeps its traceback."""

    class _Buggy(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            return request.no_such_attribute

    spec = SelectorSpec(
        kind=SelectorKind.LIST, selector=lambda **_: None, permission_classes=[_Buggy]
    )
    context = build_offline_context(user="u")
    with pytest.raises(AttributeError, match="no_such_attribute"):
        enforce_permissions(spec, context)


def test_a_spec_with_no_permission_classes_still_needs_no_request() -> None:
    """The refusal is scoped to the case that would have crashed.

    ``permission_classes is None`` is a documented no-op off HTTP, so a caller
    that dispatches without a request and declares nothing to enforce must keep
    working — refusing here would break the transport-neutral default.
    """
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: None)
    enforce_permissions(spec, OfflineContext(user="u", request=None, view=None))
