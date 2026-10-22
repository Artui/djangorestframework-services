"""``enforce_affordances``: the kernel's, reading the names the DRF edge seeds."""

from __future__ import annotations

from typing import Any

from django_service_specs.affordances.enforce_affordances import (
    enforce_affordances as kernel_enforce_affordances,
)

from rest_framework_services.dispatch.utils import EDGE_SEEDS
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS

# Drift risk: this mirrors the kernel's signature by hand. A parameter the
# kernel adds is not reachable through this name until it is added here, which
# test_a_wrapper_takes_the_kernel_s_arguments now fails on, and ``EDGE_SEEDS``
# is read at each call, so a name the edge comes to seed is reserved here with
# no change (test_affordances_read_the_edge_seeds holds the agreement for
# ``request``).


def enforce_affordances(
    spec: Any,
    pool: dict[str, Any],
    *,
    instance: Any,
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
    call_context: dict[str, Any] | None = None,
) -> None:
    """Refuse the call with ``ActionUnavailable`` if one of the spec's ``affordances`` is not met.

    django-service-specs' own ``enforce_affordances``, with ``request`` and
    ``view`` reserved beside ``reserved``: ``dispatch_spec`` seeds both itself,
    so a condition declaring either is handed it from ``pool`` here as it is
    at the call. ``call_context`` is the call's context, handed to a condition
    naming it as the kernel's function hands it. See the kernel's function for
    the order it must run in.
    """
    kernel_enforce_affordances(
        spec,
        pool,
        instance=instance,
        reserved=reserved | frozenset(EDGE_SEEDS),
        call_context=call_context,
    )
