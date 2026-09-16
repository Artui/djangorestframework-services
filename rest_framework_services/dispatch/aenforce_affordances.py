"""``aenforce_affordances`` — the async twin of ``enforce_affordances``."""

from __future__ import annotations

from typing import Any

from rest_framework_services.dispatch.enforce_affordances import enforce_affordances
from rest_framework_services.dispatch.utils import arun_off_loop
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.service_spec import ServiceSpec


async def aenforce_affordances(
    spec: ServiceSpec[Any, Any, Any],
    pool: dict[str, Any],
    *,
    instance: Any,
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
) -> None:
    """``enforce_affordances`` for the async path.

    The row check is a query and a callable condition is user code, so the whole
    evaluation runs in the executor. A spec declaring none returns before the hop,
    so declaring nothing costs the async path nothing either.
    """
    if not spec.affordances:
        return
    await arun_off_loop(enforce_affordances, spec, pool, instance=instance, reserved=reserved)


__all__ = ["aenforce_affordances"]
