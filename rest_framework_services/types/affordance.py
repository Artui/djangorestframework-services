"""``Affordance`` — one condition under which a mutation can be done right now."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.types.utils import PER_CALL_POOL_NAMES, is_row_condition


@dataclass(frozen=True)
class Affordance:
    """What can be done to this object right now, declared once on the operation.

    A condition on a
    [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] saying
    whether the mutation is possible **as a fact about the world**: an order that
    shipped cannot be cancelled, a draft with no title cannot be published, refunds
    are closed while the books are. Not a fact about the caller -- that is
    ``permission_classes``, which refuses with a 403 and is always checked first.
    An affordance refuses with
    [`ActionUnavailable`][rest_framework_services.exceptions.action_unavailable.ActionUnavailable],
    a 409.

    ```python
    ServiceSpec(
        service=cancel_order,
        affordances=[
            Affordance(
                code="order_shipped",
                reason="A shipped order cannot be cancelled.",
                when=~Q(status="shipped"),
            ),
        ],
    )
    ```

    ``when`` is true when the action **is** available, and its type decides what
    kind of condition it is -- derived, never declared, so it cannot drift from
    what it describes:

    - **An ORM boolean expression** -- a ``Q``, an ``Exists``, a lookup -- is a
      condition on the row. It reads exactly as ``Model.objects.filter(when)``
      does, relations included, and is evaluated in SQL: one query for a single
      object, and inside the list query itself when projected onto a selector's
      rows. A row is available only when the condition is true; a ``NULL`` is not
      true.
    - **A callable** is a condition on nothing in particular -- "refunds are
      closed during month-end". It is resolved through the keyword pool like any
      other spec callable and returns a ``bool``, and it sees the pool's seeds
      only: ``user``, ``request``, ``progress`` and any registered pool seed. Never
      the call's target or input, and never a client argument -- the caller does
      not get to decide whether the operation is available.

    **A Python callable over the row is refused, and that refusal is the design.**
    A predicate the ORM cannot read has to run once per row, which turns every
    list that reports availability into one query per row, and prefetching does
    not fix it: a prefetched relation answers some reads from its cache and
    silently re-queries for others. A rule that cannot be computed for fifty rows
    in one query is not an affordance -- it stays in ``preconditions``, where it
    is enforced and never advertised.

    Attributes:
        code: Stable and machine-readable: it names the rule, never the state
            that tripped it, and it does not change when the sentence is
            reworded. What a transport or a client branches on.
        reason: The sentence people and models both read -- in a rendered
            answer, and as the message of the ``ActionUnavailable`` a refused
            call raises. Write it for them: say what is not possible and, where
            it helps, what would make it possible, and keep internal state out
            of it, since every reader of the operation reads it.
        when: The condition, as above.

    Raises:
        ImproperlyConfigured: ``code`` or ``reason`` is not a non-empty string;
            ``when`` is an ORM expression that is not boolean; ``when`` is a
            callable naming ``instance``, ``collection``, ``data`` or
            ``serializer``; or ``when`` is neither an expression nor a callable.
    """

    code: str
    reason: str
    when: Any

    def __post_init__(self) -> None:
        for field_name in ("code", "reason"):
            value: Any = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ImproperlyConfigured(
                    f"Affordance.{field_name} must be a non-empty string; got {value!r}."
                )
        if is_row_condition(self.when):
            if not getattr(self.when, "conditional", False):
                raise ImproperlyConfigured(
                    f"Affordance {self.code!r}: `when` is an ORM expression but not a "
                    f"boolean one ({self.when!r}). Use a Q, an Exists, a lookup, or an "
                    "expression with output_field=BooleanField()."
                )
            return
        if not callable(self.when):
            raise ImproperlyConfigured(
                f"Affordance {self.code!r}: `when` must be an ORM boolean expression "
                f"(a condition on the row) or a callable (a condition on nothing in "
                f"particular); got {type(self.when).__name__}."
            )
        _refuse_per_call_names(self.code, self.when)


def _refuse_per_call_names(code: str, when: Callable[..., Any]) -> None:
    named = sorted(PER_CALL_POOL_NAMES.intersection(inspect.signature(when).parameters))
    if not named:
        return
    if "instance" in named:
        raise ImproperlyConfigured(
            f"Affordance {code!r}: `when` is a callable that reads `instance`. A "
            "condition on the row must be an ORM expression -- a Q or an Exists -- so "
            "it can be evaluated for a whole list in one query. A rule the ORM cannot "
            "express belongs in `preconditions`, where it is enforced and not "
            "advertised."
        )
    raise ImproperlyConfigured(
        f"Affordance {code!r}: `when` reads {', '.join(repr(n) for n in named)}, which "
        "exist only once a call is being made. An affordance is answerable without "
        "attempting the call; a rule about this call's target or input belongs in "
        "`preconditions`."
    )


__all__ = ["Affordance"]
