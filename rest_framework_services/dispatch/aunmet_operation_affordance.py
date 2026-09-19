"""``aunmet_operation_affordance`` — the async twin of ``unmet_operation_affordance``."""

from __future__ import annotations

from typing import Any

from rest_framework_services.dispatch.operation_affordances import operation_affordances
from rest_framework_services.dispatch.unmet_operation_affordance import (
    unmet_operation_affordance,
)
from rest_framework_services.dispatch.utils import arun_off_loop
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
    answered at list time -- nothing in
    [`operation_affordances`][rest_framework_services.dispatch.operation_affordances.operation_affordances]
    -- returns before the hop: a transport asks this for every operation it
    lists, and most declare nothing, so declaring nothing costs the async path
    nothing either.

    **Where the hop lands decides who closes what the condition opened.** It is a
    thread-sensitive ``sync_to_async``. Inside Django's request cycle that is the
    request's own thread, and Django closes the connections it opened when the
    request finishes. Outside it -- a long-lived agent run, a worker with its own
    event loop -- there is no request context, and unless the loop was started
    from sync code through ``async_to_sync``, asgiref runs the hop on its one
    process-wide thread. Django's connections are per thread, so a connection
    opened there stays open after this returns, the event loop's thread cannot
    reach it, and every later hop that lands on that thread inherits it. A
    caller that must release what a condition opened makes its own hop and calls
    ``unmet_operation_affordance`` inside it, releasing the connection in that
    same hop.
    """
    if not operation_affordances(spec):
        return None
    return await arun_off_loop(unmet_operation_affordance, spec, pool, reserved=reserved)


__all__ = ["aunmet_operation_affordance"]
