"""The four affordance wrappers take the kernel's ``call_context`` and pass it on.

Each wrapper mirrors the kernel's signature by hand, so a keyword the kernel
adds is unreachable through drfs' name until it is added here. The first test
is that mirror, checked; the rest hand a condition the dict the caller passed.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from django_service_specs.affordances.aenforce_affordances import (
    aenforce_affordances as kernel_aenforce_affordances,
)
from django_service_specs.affordances.aunmet_operation_affordance import (
    aunmet_operation_affordance as kernel_aunmet_operation_affordance,
)
from django_service_specs.affordances.enforce_affordances import (
    enforce_affordances as kernel_enforce_affordances,
)
from django_service_specs.affordances.unmet_operation_affordance import (
    unmet_operation_affordance as kernel_unmet_operation_affordance,
)

from rest_framework_services import (
    ServiceSpec,
    aenforce_affordances,
    aunmet_operation_affordance,
    enforce_affordances,
    unmet_operation_affordance,
)
from rest_framework_services.types.affordance import Affordance


def _parameters(fn: Any) -> list[tuple[str, Any]]:
    return [(name, param.default) for name, param in inspect.signature(fn).parameters.items()]


@pytest.mark.parametrize(
    ("ours", "kernels"),
    [
        (enforce_affordances, kernel_enforce_affordances),
        (aenforce_affordances, kernel_aenforce_affordances),
        (unmet_operation_affordance, kernel_unmet_operation_affordance),
        (aunmet_operation_affordance, kernel_aunmet_operation_affordance),
    ],
    ids=[
        "enforce_affordances",
        "aenforce_affordances",
        "unmet_operation_affordance",
        "aunmet_operation_affordance",
    ],
)
def test_a_wrapper_takes_the_kernel_s_arguments(ours: Any, kernels: Any) -> None:
    assert _parameters(ours) == _parameters(kernels)


def _spec(seen: list[dict[str, Any]]) -> ServiceSpec[Any, Any, Any]:
    def open_(*, call_context: dict[str, Any]) -> bool:
        seen.append(call_context)
        return True

    return ServiceSpec(
        service=lambda: None,
        atomic=False,
        affordances=[Affordance(code="closed", reason="Closed.", when=open_)],
    )


def _pool() -> dict[str, Any]:
    return {"request": None, "view": None, "user": None}


def test_enforce_affordances_hands_a_condition_the_dict_passed() -> None:
    seen: list[dict[str, Any]] = []
    context: dict[str, Any] = {}

    enforce_affordances(_spec(seen), _pool(), instance=None, call_context=context)

    assert seen == [context]
    assert seen[0] is context


async def test_aenforce_affordances_hands_a_condition_the_dict_passed() -> None:
    seen: list[dict[str, Any]] = []
    context: dict[str, Any] = {}

    await aenforce_affordances(_spec(seen), _pool(), instance=None, call_context=context)

    assert seen[0] is context


def test_unmet_operation_affordance_hands_a_condition_the_dict_passed() -> None:
    seen: list[dict[str, Any]] = []
    context: dict[str, Any] = {}

    assert unmet_operation_affordance(_spec(seen), _pool(), call_context=context) is None
    assert seen[0] is context


async def test_aunmet_operation_affordance_hands_a_condition_the_dict_passed() -> None:
    seen: list[dict[str, Any]] = []
    context: dict[str, Any] = {}

    assert await aunmet_operation_affordance(_spec(seen), _pool(), call_context=context) is None
    assert seen[0] is context
