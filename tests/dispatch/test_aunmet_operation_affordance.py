"""``aunmet_operation_affordance`` is django-service-specs' own, reading the names the edge seeds.

What it answers is the kernel's and is tested there; the twins' signatures are
held equal in ``tests/test_sync_async_parity.py``, and the names it reserves
beside the caller's in ``tests/dispatch/test_affordances_read_the_edge_seeds.py``.
"""

from __future__ import annotations

from asgiref.sync import async_to_sync

from rest_framework_services import ServiceSpec, aunmet_operation_affordance
from rest_framework_services.types.affordance import Affordance

_FROZEN = Affordance(code="frozen", reason="Frozen.", when=lambda: False)


def test_it_answers_what_the_kernel_answers() -> None:
    spec = ServiceSpec(service=lambda: None, affordances=[_FROZEN])

    assert async_to_sync(aunmet_operation_affordance)(spec, {"user": None}) is _FROZEN
