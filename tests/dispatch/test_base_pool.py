"""Tests for ``base_pool``."""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_services.dispatch.base_pool import base_pool
from rest_framework_services.dispatch.null_progress import null_progress
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS


def test_seeds_are_the_documented_three() -> None:
    request = object()
    user = object()
    assert base_pool(user=user, request=request) == {
        "request": request,
        "user": user,
        "progress": null_progress,
    }


def test_extra_entries_join_the_seeds() -> None:
    """An adapter can build its whole pool here instead of restating the seeds."""
    own_entries: dict[str, Any] = {"tenant": "acme", "trace_id": 7}
    pool = base_pool(user="u", request="r", **own_entries)
    assert pool == {
        "request": "r",
        "user": "u",
        "progress": null_progress,
        "tenant": "acme",
        "trace_id": 7,
    }


def test_an_entry_named_after_a_seed_collides_loudly() -> None:
    """The reason to route adapter entries through here rather than a dict literal.

    A literal would let the entry outrank the transport's authenticated value in
    silence; spreading it into this call cannot.
    """
    own_entries: dict[str, Any] = {"user": "spoofed"}
    with pytest.raises(TypeError):
        base_pool(user="real", request="r", **own_entries)


def test_a_registered_seed_is_resolved_into_the_pool() -> None:
    seeds = DEFAULT_POOL_SEEDS.extend(tenant=lambda *, user: f"tenant-of-{user}")
    assert base_pool(user="u", request="r", seeds=seeds)["tenant"] == "tenant-of-u"


def test_a_resolver_receives_only_the_pool_entries_it_declares() -> None:
    """Seeds bind by the same declare-to-receive rule as every other spec callable."""
    seen: dict[str, Any] = {}

    def tenant_of(*, user: Any) -> str:
        seen["kwargs"] = {"user": user}
        return "acme"

    base_pool(user="u", request="r", seeds=DEFAULT_POOL_SEEDS.extend(tenant=tenant_of))
    assert seen == {"kwargs": {"user": "u"}}


def test_a_resolver_declaring_var_keyword_receives_the_whole_pool() -> None:
    captured: dict[str, Any] = {}

    def everything(**pool: Any) -> str:
        captured.update(pool)
        return "x"

    base_pool(user="u", request="r", seeds=DEFAULT_POOL_SEEDS.extend(seed=everything))
    assert set(captured) == {"user", "request", "progress"}


def test_a_seed_cannot_read_another_seed() -> None:
    """Resolvers bind against the pool *before* any seed is written.

    Deliberate: it makes the result independent of registration order, so
    re-ordering two ``extend`` calls cannot silently change what a callable sees.
    A seed that needs another composes the two inside its own resolver.
    """

    def wants_a_sibling(**pool: Any) -> Any:
        return "tenant" in pool

    seeds = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme").extend(saw=wants_a_sibling)
    assert base_pool(user="u", request="r", seeds=seeds)["saw"] is False


def test_a_seed_colliding_with_a_spread_entry_is_refused() -> None:
    """``**extra`` is per-call and a seed is ambient; one silently outranking the
    other is the ambiguity the registry exists to remove."""
    seeds = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "registered")
    with pytest.raises(TypeError, match="tenant"):
        base_pool(user="u", request="r", seeds=seeds, tenant="spread")
