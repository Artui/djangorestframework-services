"""``aunmet_operation_affordance`` is django-service-specs' own, re-exported at this path.

What it answers, and what its executor hop costs, are the kernel's and are tested
there; the twins' signatures are held equal in ``tests/test_sync_async_parity.py``.
"""

from __future__ import annotations

from django_service_specs.affordances.aunmet_operation_affordance import (
    aunmet_operation_affordance as kernel_aunmet_operation_affordance,
)

from rest_framework_services import aunmet_operation_affordance


def test_it_is_the_kernels_function() -> None:
    assert aunmet_operation_affordance is kernel_aunmet_operation_affordance
