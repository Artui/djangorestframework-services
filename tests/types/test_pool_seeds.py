"""``PoolSeeds`` and its two values: what a drfs caller sees change now that they are the kernel's.

The registry's rules are django-service-specs' and tested there. These pin the
reserved set it checks against, which is the kernel's and not drfs' old one,
and the one resolver shape it refuses when it is registered.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS


def test_the_reserved_set_is_the_kernels() -> None:
    """``result`` and ``queryset`` join it; ``request`` and ``serializer`` leave
    it, because they are an HTTP adapter's values and not the kernel's."""
    assert (
        frozenset({"user", "data", "instance", "collection", "result", "queryset", "progress"})
        == RESERVED_POOL_SEEDS
    )


@pytest.mark.parametrize("name", ["result", "queryset"])
def test_a_seed_named_for_a_later_steps_value_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match=name):
        DEFAULT_POOL_SEEDS.extend(**{name: lambda: "nope"})


@pytest.mark.parametrize("name", ["request", "serializer"])
def test_a_seed_named_for_an_http_adapters_value_is_registrable(name: str) -> None:
    extended = DEFAULT_POOL_SEEDS.extend(**{name: lambda: "mine"})

    assert name in extended.names


async def _async_tenant(**_: Any) -> str:
    return "acme"


def test_an_async_resolver_is_refused_when_registered() -> None:
    """A resolver is called from sync code and never awaited, and an unawaited
    coroutine is truthy, so whatever read the seed would read nonsense."""
    with pytest.raises(ImproperlyConfigured, match="async resolver"):
        DEFAULT_POOL_SEEDS.extend(tenant=_async_tenant)
