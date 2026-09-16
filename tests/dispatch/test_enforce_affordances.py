"""``enforce_affordances`` called directly, as a transport that bypasses the core must.

``dispatch_spec`` runs this check itself, so everything about *which* condition
answers and *how* a row condition is queried is covered where the dispatcher is
(``tests/dispatch/test_affordances.py``). What belongs here is the contract a caller
without the dispatcher relies on: handed the pool it is about to run a service with,
the function refuses with the declaration's own words, lets a met or undeclared
condition through, and keeps the caller's arguments away from the condition.
"""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_services import (
    ActionUnavailable,
    Affordance,
    ServiceSpec,
    enforce_affordances,
)


def _service(**_: Any) -> None: ...


def test_an_unmet_condition_refuses_with_its_code_and_reason() -> None:
    spec = ServiceSpec(
        service=_service,
        affordances=[
            Affordance(code="books_closed", reason="The books are closed.", when=lambda: False)
        ],
    )

    with pytest.raises(ActionUnavailable) as exc:
        enforce_affordances(spec, {"user": None}, instance=None)

    assert exc.value.code == "books_closed"
    assert exc.value.message == "The books are closed."


@pytest.mark.parametrize(
    "affordances",
    [None, [Affordance(code="open", reason="Closed.", when=lambda: True)]],
    ids=["none-declared", "condition-met"],
)
def test_a_met_or_undeclared_condition_lets_the_call_through(
    affordances: list[Affordance] | None,
) -> None:
    spec = ServiceSpec(service=_service, affordances=affordances)

    assert enforce_affordances(spec, {"user": None}, instance=None) is None


def test_the_callers_arguments_never_reach_a_condition() -> None:
    """A direct caller hands over the whole pool, arguments included.

    The condition must still see only the seeds -- otherwise whoever supplies the
    arguments decides whether the call is available.
    """
    seen: dict[str, Any] = {}

    def when(**kwargs: Any) -> bool:
        seen.update(kwargs)
        return True

    spec = ServiceSpec(
        service=_service, affordances=[Affordance(code="open", reason="Closed.", when=when)]
    )

    enforce_affordances(spec, {"user": "alice", "available": True, "title": "x"}, instance=None)

    assert "user" in seen
    assert "available" not in seen
    assert "title" not in seen
