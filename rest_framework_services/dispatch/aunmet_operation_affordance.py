"""``aunmet_operation_affordance`` — the async twin of ``unmet_operation_affordance``."""

from __future__ import annotations

from typing import Any

from rest_framework_services.dispatch.unmet_operation_affordance import (
    unmet_operation_affordance,
)
from rest_framework_services.dispatch.utils import arun_off_loop, operation_conditions
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


async def aunmet_operation_affordance(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    pool: dict[str, Any],
    *,
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
) -> Affordance | None:
    """``unmet_operation_affordance`` for the async path.

    A callable condition is user code and may query, so the evaluation runs in
    the executor, as ``aenforce_affordances`` runs it. A spec with no condition
    answered at list time -- none declared, only conditions on the row, or a
    ``SelectorSpec`` -- returns before the hop: a transport asks this for every
    operation it lists, and most declare nothing, so declaring nothing costs the
    async path nothing either.
    """
    if not operation_conditions(spec):
        return None
    return await arun_off_loop(unmet_operation_affordance, spec, pool, reserved=reserved)


__all__ = ["aunmet_operation_affordance"]
