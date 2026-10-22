"""``unmet_operation_affordance``: the kernel's, reading the names the DRF edge seeds."""

from __future__ import annotations

from typing import Any

from django_service_specs.affordances.unmet_operation_affordance import (
    unmet_operation_affordance as kernel_unmet_operation_affordance,
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


def unmet_operation_affordance(
    spec: Any,
    pool: dict[str, Any],
    *,
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
    call_context: dict[str, Any] | None = None,
) -> Affordance | None:
    """The first of the spec's operation-scope affordances not met now, or ``None``.

    django-service-specs' own ``unmet_operation_affordance``, with ``request``
    and ``view`` reserved beside ``reserved``: ``dispatch_spec`` seeds both
    itself, so a condition declaring either is handed it at the call, and is
    handed it here from ``pool`` too. Pass ``reserved=seeds.reserved`` for the
    seeds the call registers, and listing time reads the names call time does.
    ``call_context`` is the call's context, handed to a condition naming it as
    the kernel's function hands it. See the kernel's function for everything
    else it answers.
    """
    return kernel_unmet_operation_affordance(
        spec, pool, reserved=reserved | frozenset(EDGE_SEEDS), call_context=call_context
    )
