"""``aenforce_affordances``: the kernel's, reading the names the DRF edge seeds."""

from __future__ import annotations

from typing import Any

from django_service_specs.affordances.aenforce_affordances import (
    aenforce_affordances as kernel_aenforce_affordances,
)

from rest_framework_services.dispatch.utils import EDGE_SEEDS
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS

# Drift risk: this mirrors the kernel's signature by hand. A parameter the
# kernel adds is not reachable through this name until it is added here, which
# test_a_wrapper_takes_the_kernel_s_arguments now fails on, and ``EDGE_SEEDS``
# is read at each call, so a name the edge comes to seed is reserved here with
# no change (test_affordances_read_the_edge_seeds holds the agreement for
# ``request``).


async def aenforce_affordances(
    spec: Any,
    pool: dict[str, Any],
    *,
    instance: Any,
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
    call_context: dict[str, Any] | None = None,
) -> None:
    """Async ``enforce_affordances``, with ``request`` and ``view`` reserved the same way."""
    await kernel_aenforce_affordances(
        spec,
        pool,
        instance=instance,
        reserved=reserved | frozenset(EDGE_SEEDS),
        call_context=call_context,
    )
