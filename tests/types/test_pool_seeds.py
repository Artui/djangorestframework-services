"""Tests for ``PoolSeeds``."""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS, PoolSeeds
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS


def test_the_default_registry_is_empty() -> None:
    """So a project that registers nothing behaves exactly as before."""
    assert DEFAULT_POOL_SEEDS.names == frozenset()
    assert DEFAULT_POOL_SEEDS.reserved == RESERVED_POOL_SEEDS


def test_extend_returns_a_new_registry_and_leaves_the_original_alone() -> None:
    """No shared global state to leak across mounts or tests."""
    extended = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")
    assert extended.names == frozenset({"tenant"})
    assert DEFAULT_POOL_SEEDS.names == frozenset()
    assert extended is not DEFAULT_POOL_SEEDS


def test_a_registered_name_joins_the_reserved_set() -> None:
    """The whole point: a registered seed gets the protection the built-ins get."""
    extended = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")
    assert extended.reserved == RESERVED_POOL_SEEDS | {"tenant"}


def test_registering_a_built_in_name_is_refused() -> None:
    """A seed named ``user`` would shadow the value the transport authenticated."""
    with pytest.raises(ValueError, match="user"):
        DEFAULT_POOL_SEEDS.extend(user=lambda: "nope")


def test_registering_the_same_name_twice_is_refused() -> None:
    """Silent last-wins here reproduces the bug the reservation exists to prevent."""
    once = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")
    with pytest.raises(ValueError, match="tenant"):
        once.extend(tenant=lambda: "other")


def test_resolvers_are_exposed_in_registration_order() -> None:
    """Stable order, so an error message or a listing is reproducible.

    Resolution itself lives in ``base_pool`` rather than here — it needs the
    declare-to-receive helper, and ``types/`` may not import from the behavioural
    packages. See ``tests/dispatch/test_base_pool.py`` for the binding rules.
    """
    extended = PoolSeeds().extend(a=lambda: 1).extend(b=lambda: 2)
    assert list(extended.resolvers()) == ["a", "b"]


def test_resolvers_round_trips_the_registered_callable() -> None:
    def tenant_of(*, user: Any) -> str:
        return f"tenant-of-{user}"

    extended = DEFAULT_POOL_SEEDS.extend(tenant=tenant_of)
    assert extended.resolvers() == {"tenant": tenant_of}
