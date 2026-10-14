"""``SpecRegistry``: what a drfs caller sees change now that it is the kernel's.

The registry is django-service-specs' and its rules are tested there. These pin
what registration now takes and refuses: a kernel spec, compiled once when it is
registered; no ``agent_contract=``; and no spec that declares no permissions.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs.authorization.unrestricted import Unrestricted
from django_service_specs.specs.selector_spec import SelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec
from django_service_specs.types.selector_kind import SelectorKind

from rest_framework_services.registry.spec_registry import SpecRegistry


def _noop(**_: Any) -> None: ...


def test_a_kernel_spec_is_registered_and_sorted_by_what_it_compiles_to() -> None:
    write = ServiceSpec(service=_noop, permissions=[Unrestricted()])
    read = SelectorSpec(kind=SelectorKind.LIST, selector=_noop, permissions=[Unrestricted()])
    registry = SpecRegistry()

    registry.register("write", write)
    registry.register("read", read)

    assert [entry.spec for entry in registry.mutations()] == [write]
    assert [entry.spec for entry in registry.queries()] == [read]


def test_registration_takes_no_agent_contract() -> None:
    """The contract is the spec's to declare, so a project still passing one
    meets Python's ``TypeError`` for an unexpected keyword."""
    spec = ServiceSpec(service=_noop, permissions=[Unrestricted()])

    with pytest.raises(TypeError, match="agent_contract"):
        SpecRegistry().register("x", spec, agent_contract=None)  # ty: ignore[unknown-argument]


def test_a_spec_with_no_permissions_is_refused() -> None:
    """Off HTTP no view's policy covers an undeclared spec, so one meant to be
    open says so with ``Unrestricted()``."""
    with pytest.raises(ImproperlyConfigured, match="declares no permissions"):
        SpecRegistry().register("x", ServiceSpec(service=_noop))
