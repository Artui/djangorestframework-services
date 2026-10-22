"""``aunmet_operation_affordance``: the kernel's, reading the names the DRF edge seeds."""

from __future__ import annotations

from typing import Any

from django_service_specs.affordances.aunmet_operation_affordance import (
    aunmet_operation_affordance as kernel_aunmet_operation_affordance,
)
from django_service_specs.types.affordance import Affordance

from rest_framework_services.dispatch.utils import EDGE_SEEDS
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS

# Drift risk: this mirrors the kernel's signature by hand. A parameter the
# kernel adds is not reachable through this name until it is added here, which
# test_a_wrapper_takes_the_kernel_s_arguments now fails on, and ``EDGE_SEEDS``
# is read at each call, so a name the edge comes to seed is reserved here with
# no change (test_affordances_read_the_edge_seeds holds the agreement for
# ``request``).


async def aunmet_operation_affordance(
    spec: Any,
    pool: dict[str, Any],
    *,
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
    call_context: dict[str, Any] | None = None,
) -> Affordance | None:
    """Async ``unmet_operation_affordance``, with ``request`` and ``view`` reserved the same way."""
    return await kernel_aunmet_operation_affordance(
        spec, pool, reserved=reserved | frozenset(EDGE_SEEDS), call_context=call_context
    )
