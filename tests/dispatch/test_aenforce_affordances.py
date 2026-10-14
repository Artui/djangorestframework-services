"""``aenforce_affordances`` is django-service-specs' own, re-exported at this path.

What it refuses, and what its executor hop costs, are the kernel's and are tested
there; the twins' signatures are held equal in ``tests/test_sync_async_parity.py``.
"""

from __future__ import annotations

from django_service_specs.affordances.aenforce_affordances import (
    aenforce_affordances as kernel_aenforce_affordances,
)

from rest_framework_services import aenforce_affordances


def test_it_is_the_kernels_function() -> None:
    assert aenforce_affordances is kernel_aenforce_affordances
