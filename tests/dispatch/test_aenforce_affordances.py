"""``aenforce_affordances`` called directly, the async twin of ``enforce_affordances``.

The twins' signatures are held equal in ``tests/test_sync_async_parity.py`` and the
executor hop is counted in ``tests/dispatch/test_affordances.py``; this pins that
the awaited form refuses and lets through exactly as the sync one does.
"""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_services import (
    ActionUnavailable,
    Affordance,
    ServiceSpec,
    aenforce_affordances,
)


def _service(**_: Any) -> None: ...


async def test_an_unmet_condition_refuses_with_its_code_and_reason() -> None:
    spec = ServiceSpec(
        service=_service,
        affordances=[
            Affordance(code="books_closed", reason="The books are closed.", when=lambda: False)
        ],
    )

    with pytest.raises(ActionUnavailable) as exc:
        await aenforce_affordances(spec, {"user": None}, instance=None)

    assert exc.value.code == "books_closed"
    assert exc.value.message == "The books are closed."


async def test_a_met_or_undeclared_condition_lets_the_call_through() -> None:
    met = Affordance(code="open", reason="Closed.", when=lambda: True)

    assert (
        await aenforce_affordances(ServiceSpec(service=_service), {"user": None}, instance=None)
        is None
    )
    assert (
        await aenforce_affordances(
            ServiceSpec(service=_service, affordances=[met]), {"user": None}, instance=None
        )
        is None
    )
