"""``aenforce_affordances`` is django-service-specs' own, reading the names the edge seeds.

What it refuses, and what its executor hop costs, are the kernel's and are tested
there; the twins' signatures are held equal in ``tests/test_sync_async_parity.py``,
and the names it reserves beside the caller's in
``tests/dispatch/test_affordances_read_the_edge_seeds.py``.
"""

from __future__ import annotations

import pytest
from asgiref.sync import async_to_sync

from rest_framework_services import ActionUnavailable, ServiceSpec, aenforce_affordances
from rest_framework_services.types.affordance import Affordance


def test_it_refuses_what_the_kernel_refuses() -> None:
    spec = ServiceSpec(
        service=lambda: None,
        affordances=[Affordance(code="frozen", reason="Frozen.", when=lambda: False)],
    )

    with pytest.raises(ActionUnavailable) as caught:
        async_to_sync(aenforce_affordances)(spec, {"user": None}, instance=None)

    assert caught.value.code == "frozen"
