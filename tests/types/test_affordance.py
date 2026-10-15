"""``Affordance``: what a drfs caller sees change now that it is the kernel's.

The declaration's own rules are django-service-specs' and tested there. These
are the two places where a condition drfs accepted when it was declared is now
refused at that point instead, and the kernel's wording of a refusal drfs
already gave.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.types.affordance import Affordance


def _reads_result(result: Any) -> bool:
    return bool(result)


def _reads_queryset(queryset: Any) -> bool:
    return queryset is not None


@pytest.mark.parametrize(
    ("when", "name"),
    [(_reads_result, "result"), (_reads_queryset, "queryset")],
    ids=["result", "queryset"],
)
def test_a_condition_reading_a_later_steps_value_is_refused_when_declared(
    when: Any, name: str
) -> None:
    """``result`` and ``queryset`` exist only once a call is being made, like
    ``data`` and ``instance``, so the declaration refuses a condition naming
    them rather than a call failing with ``TypeError`` when it is asked."""
    with pytest.raises(ImproperlyConfigured, match=f"reads '{name}'"):
        Affordance(code="c", reason="r", when=when)


async def _async_condition() -> bool:
    return False


def test_an_async_condition_is_refused_when_declared() -> None:
    """A condition is read for truth from sync code, and an unawaited answer is
    truthy, so an ``async def`` one would report every unmet condition as met."""
    with pytest.raises(ImproperlyConfigured, match="async callable"):
        Affordance(code="c", reason="r", when=_async_condition)


def _reads_instance(instance: Any) -> bool:
    return instance is not None


def _reads_data(data: Any) -> bool:
    return bool(data)


_PER_CALL = {
    "instance": (
        _reads_instance,
        "A rule the ORM cannot express belongs in the service, where it is enforced and "
        "not advertised.",
    ),
    "data": (
        _reads_data,
        "a rule about this call's target or input belongs in the service.",
    ),
}


@pytest.mark.parametrize(("when", "ending"), _PER_CALL.values(), ids=_PER_CALL.keys())
def test_a_per_call_condition_is_sent_to_the_service(when: Any, ending: str) -> None:
    """The kernel's words: the rule belongs in the service, where 0.57.0 said
    ``preconditions``, and a hyphen where it wrote a double dash."""
    with pytest.raises(ImproperlyConfigured) as excinfo:
        Affordance(code="c", reason="r", when=when)

    message = str(excinfo.value)
    assert message.endswith(ending)
    assert "--" not in message
