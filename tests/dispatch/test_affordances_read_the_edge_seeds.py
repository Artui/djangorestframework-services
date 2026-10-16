"""Listing time and call time answer an affordance from the same names.

``dispatch_spec`` seeds ``request`` and ``view`` itself, so a condition
declaring either is handed it at the call. The package's affordance helpers
add those names to the ``reserved`` set a transport passes, so a pool built
with ``base_pool`` and ``reserved=seeds.reserved`` answers the condition at
listing time as the call answers it, with no change to the transport.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.http import HttpRequest
from rest_framework.permissions import AllowAny
from rest_framework.request import Request

from rest_framework_services import (
    DEFAULT_POOL_SEEDS,
    ActionUnavailable,
    ServiceSpec,
    aenforce_affordances,
    aunmet_operation_affordance,
    dispatch_spec,
    enforce_affordances,
    unmet_operation_affordance,
)
from rest_framework_services.types.affordance import Affordance

_SEEN: list[str] = []


def _spec(open_: bool) -> ServiceSpec[Any, Any, Any]:
    def staff_only(request: Any, view: Any) -> bool:
        _SEEN.append(type(request).__name__)
        return open_

    return ServiceSpec(
        service=lambda: {"ok": 1},
        atomic=False,
        permission_classes=[AllowAny],
        affordances=[Affordance(code="closed", reason="Closed.", when=staff_only)],
    )


def _pool(request: Request) -> dict[str, Any]:
    return {"request": request, "view": None, "user": None}


@pytest.mark.parametrize("open_", [True, False], ids=["met", "unmet"])
def test_listing_and_the_call_agree_on_a_condition_reading_the_request(open_: bool) -> None:
    spec = _spec(open_)
    request = Request(HttpRequest())
    _SEEN.clear()

    listed = unmet_operation_affordance(spec, _pool(request), reserved=DEFAULT_POOL_SEEDS.reserved)
    try:
        dispatch_spec(spec, user=None, params={}, request=request)
        called = None
    except ActionUnavailable as exc:
        called = exc.code

    assert (listed.code if listed is not None else None) == called
    assert called == (None if open_ else "closed")
    assert _SEEN == ["Request", "Request"]


def test_the_async_listing_reads_the_request_too() -> None:
    request = Request(HttpRequest())

    listed = async_to_sync(aunmet_operation_affordance)(
        _spec(False), _pool(request), reserved=DEFAULT_POOL_SEEDS.reserved
    )

    assert listed is not None
    assert listed.code == "closed"


def test_with_no_reserved_set_the_listing_reads_the_request() -> None:
    assert unmet_operation_affordance(_spec(True), _pool(Request(HttpRequest()))) is None


@pytest.mark.parametrize("enforce", ["sync", "async"])
def test_enforcing_reads_the_request(enforce: str) -> None:
    pool = _pool(Request(HttpRequest()))
    reserved = DEFAULT_POOL_SEEDS.reserved

    with pytest.raises(ActionUnavailable):
        if enforce == "sync":
            enforce_affordances(_spec(False), pool, instance=None, reserved=reserved)
        else:
            async_to_sync(aenforce_affordances)(
                _spec(False), pool, instance=None, reserved=reserved
            )
