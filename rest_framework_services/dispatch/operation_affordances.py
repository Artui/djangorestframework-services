"""``operation_affordances`` — the affordances a spec declares that need no row."""

from __future__ import annotations

from typing import Any

from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.utils import is_row_condition


def operation_affordances(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
) -> tuple[Affordance, ...]:
    """The operation-scope affordances ``spec`` declares, in declaration order.

    The callable ones: the conditions the capability manifest calls
    ``"operation"`` scope, answered without a row. A condition on the row is left
    out, and so is anything on a ``SelectorSpec``, which answers ``()``: its
    ``affordances`` maps names to *other* operations, to be projected onto its
    rows, and reading that mapping as the selector's own conditions would answer
    a question about some other operation. A spec declaring none answers ``()``
    too.

    A transport uses this to decide cheaply whether there is anything to ask at
    list time before it builds a pool or takes a thread hop: most operations
    declare nothing, and an empty answer means
    [`unmet_operation_affordance`][rest_framework_services.dispatch.unmet_operation_affordance.unmet_operation_affordance]
    would return ``None`` without running anything. Both that function and its
    async twin select their conditions here, so a transport's shortcut and the
    answer it skips cannot disagree about which conditions there are.
    """
    if isinstance(spec, SelectorSpec):
        return ()
    return tuple(
        affordance for affordance in spec.affordances or () if not is_row_condition(affordance.when)
    )


__all__ = ["operation_affordances"]
