"""``enforce_affordances`` — refuse a call whose declared affordances are not met."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model

from rest_framework_services.dispatch.utils import answer_operation_condition
from rest_framework_services.exceptions.action_unavailable import ActionUnavailable
from rest_framework_services.exceptions.service_not_found import ServiceNotFound
from rest_framework_services.selectors.utils import affordance_expression
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.utils import is_row_condition


def enforce_affordances(
    spec: ServiceSpec[Any, Any, Any],
    pool: dict[str, Any],
    *,
    instance: Any,
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
) -> None:
    """Refuse the call if one of the spec's ``affordances`` is not met.

    ``dispatch_spec`` and ``adispatch_spec`` call this themselves. A transport that
    runs a service **without** them -- a chain that owns the transaction and hands
    each step its own pool, say -- must call it too, exactly as it must call
    [`enforce_permissions`][rest_framework_services.dispatch.enforce_permissions.enforce_permissions],
    or a condition the direct path refuses is skipped on that one. ``pool`` is the
    kwargs pool the service is about to receive and ``instance`` its resolved
    target, ``None`` for an operation with none. It raises
    [`ActionUnavailable`][rest_framework_services.exceptions.action_unavailable.ActionUnavailable]
    carrying the unmet affordance's ``reason`` and ``code``.

    Callers must invoke this **after** the target guard and **before**
    the spec's ``preconditions``: permissions -> target resolution -> validation ->
    affordances -> preconditions -> service. After access because a refusal
    describes the row's state, and telling a caller who may not see the row what
    state it is in is a disclosure; before preconditions because an affordance is
    the declared, advertisable form of the same kind of rule, and the one a client
    was told about should be the one that answers.

    Declaration order decides which refusal a caller sees, and nothing past the
    first unmet condition runs. Every condition on the row is answered together
    by one query the first time one is reached. A callable condition sees
    ``ambient_pool`` -- the seeds, never the call's target, input or client
    arguments -- so a ``**kwargs`` catch-all is held to the same rule the
    declaration enforces on a named parameter. ``reserved`` is this dispatch's
    seed set, registered seeds included. A callable's result is read for truth,
    so one that returns nothing refuses rather than allows.
    """
    affordances: Sequence[Affordance] | None = spec.affordances
    if not affordances:
        return
    row_flags: dict[int, bool] | None = None
    for index, affordance in enumerate(affordances):
        if is_row_condition(affordance.when):
            if row_flags is None:
                row_flags = _row_affordance_flags(instance, affordances)
            available: Any = row_flags[index]
        else:
            available = answer_operation_condition(affordance.when, pool, reserved=reserved)
        if not available:
            raise ActionUnavailable(affordance.reason, code=affordance.code)


def _row_affordance_flags(instance: Any, affordances: Sequence[Affordance]) -> dict[int, bool]:
    """Every row condition's answer for ``instance``, by declaration index, in one query.

    The same expression the list projection annotates, narrowed to one primary
    key, so a row reported available is the row this check lets through.
    """
    if not isinstance(instance, Model):
        raise ImproperlyConfigured(
            "An affordance condition on the row needs a resolved model instance, and this "
            f"dispatch resolved {type(instance).__name__}. Declare row conditions only on "
            "an operation that targets one row (an update, a destroy, a detail action)."
        )
    model = type(instance)
    aliases: dict[str, int] = {
        f"affordance__{index}": index
        for index, affordance in enumerate(affordances)
        if is_row_condition(affordance.when)
    }
    row = (
        model._base_manager.filter(pk=instance.pk)
        .annotate(
            **{
                alias: affordance_expression(model, affordances[index].when)
                for alias, index in aliases.items()
            }
        )
        .values_list(*aliases)
        .first()
    )
    if row is None:
        # Resolved a moment ago and gone now: the answer to "can this be done to
        # it" is that there is nothing to do it to.
        raise ServiceNotFound()
    return {index: bool(flag) for index, flag in zip(aliases.values(), row, strict=True)}


__all__ = ["enforce_affordances"]
