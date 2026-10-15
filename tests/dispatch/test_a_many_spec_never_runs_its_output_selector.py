"""A ``many=True`` service spec presents its service's list and never runs its output selector.

The output selector's ``output_serializer`` presents each row of what the
service returned, whatever the selector's kind; its ``selector`` re-reads a
single result, which a list of rows is not, so it is never called.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceCreateView,
    ServiceSpec,
    adispatch_spec,
    dispatch_spec,
)


class _Order(serializers.Serializer):
    number = serializers.CharField()


def _spec(kind: SelectorKind, calls: list[str]) -> ServiceSpec[Any, Any, Any]:
    def placed(*, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [dict(row) for row in data]

    def refetch(**_pool: Any) -> Any:
        calls.append("refetched")
        return {"number": "Z-9"}

    return ServiceSpec(
        service=placed,
        atomic=False,
        many=True,
        input_serializer=_Order,
        permission_classes=[],
        output_selector_spec=SelectorSpec(kind=kind, selector=refetch, output_serializer=_Order),
    )


_KINDS = pytest.mark.parametrize("kind", [SelectorKind.LIST, SelectorKind.RETRIEVE])
_ROWS = [{"number": "A-1"}, {"number": "A-2"}]


@_KINDS
@pytest.mark.parametrize(
    "call",
    [dispatch_spec, async_to_sync(adispatch_spec)],
    ids=["sync", "async"],
)
def test_off_http_the_service_s_rows_are_the_value(
    call: Callable[..., DispatchResult], kind: SelectorKind
) -> None:
    calls: list[str] = []

    result = call(_spec(kind, calls), user=None, params=_ROWS)

    assert result.value == _ROWS
    assert calls == []


def test_over_a_live_view_the_service_s_rows_are_presented() -> None:
    """A ``LIST`` output selector beside no collection lookup is refused at ``as_view()``."""
    calls: list[str] = []
    spec = _spec(SelectorKind.RETRIEVE, calls)
    view = type("_View", (ServiceCreateView,), {"spec": spec}).as_view()

    response = view(APIRequestFactory().post("/", _ROWS, format="json"))

    assert response.status_code == 201, response.data
    assert response.data == _ROWS
    assert calls == []


@_KINDS
@pytest.mark.parametrize("selector", [True, False], ids=["re-read", "no-re-read"])
def test_a_many_service_s_none_is_presented_whatever_its_output_declares(
    kind: SelectorKind, selector: bool
) -> None:
    spec = ServiceSpec(
        service=lambda *, data: None,
        atomic=False,
        many=True,
        input_serializer=_Order,
        permission_classes=[],
        output_selector_spec=SelectorSpec(
            kind=kind, selector=(lambda: None) if selector else None, output_serializer=_Order
        ),
    )

    assert dispatch_spec(spec, user=None, params=_ROWS).value is None
