"""``Affordance``: what a drfs caller sees change now that it is the kernel's.

The declaration's own rules are django-service-specs' and tested there. These
are the two places where a condition drfs accepted when it was declared is now
refused at that point instead.
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
