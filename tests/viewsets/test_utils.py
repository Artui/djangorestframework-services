"""Unit tests for the action-spec resolution helpers.

The type mismatches below are refused by ``as_view()`` now, so these guards are
only reachable when ``action_specs`` is built or swapped after the view is
wired. Exercised directly, because that is the only way left to reach them.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services import SelectorKind, SelectorSpec, ServiceSpec
from rest_framework_services.viewsets.utils import (
    resolve_action_selector_spec,
    resolve_action_service_spec,
)


def test_a_write_action_resolved_to_a_selector_spec_raises() -> None:
    specs: dict[str, Any] = {"create": SelectorSpec(kind=SelectorKind.LIST, selector=lambda: None)}
    with pytest.raises(ImproperlyConfigured, match="must be a ServiceSpec"):
        resolve_action_service_spec(specs, "create", "POST", view=object())


def test_a_read_action_resolved_to_a_service_spec_raises() -> None:
    specs: dict[str, Any] = {"list": ServiceSpec(service=lambda: None)}
    with pytest.raises(ImproperlyConfigured, match="must be a SelectorSpec"):
        resolve_action_selector_spec(specs, "list")
