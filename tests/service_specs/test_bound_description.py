"""The call's description is the serializer that validates only where it is bound exactly so.

``bound_description`` hands the Validator the serializer the call described
its input with where that one was built with the same ``data`` object, the
same row, the same ``partial`` and ``many``, and a context holding the same
objects under the same keys, and never one that has validated already. Each
case changes one of those and is answered with none, so the Validator builds
its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from rest_framework import serializers

from rest_framework_services.service_specs.utils import (
    EDGE_CALL,
    DescribeBinding,
    EdgeCallState,
    bound_description,
    describe_serializer,
    describe_with,
)


class _In(serializers.Serializer):
    title = serializers.CharField()


class _Other(serializers.Serializer):
    name = serializers.CharField()


_ROW = object()
_BODY = {"title": "a"}
_HELD = object()


def _context(*_arguments: Any, whole_pool: bool = False) -> dict[str, Any]:
    return {"held": _HELD}


@pytest.fixture
def state() -> Iterator[EdgeCallState]:
    state = EdgeCallState(describe_context=_context)
    describe_with(state, DescribeBinding(_In, _BODY, _ROW, partial=False))
    token = EDGE_CALL.set(state)
    try:
        yield state
    finally:
        EDGE_CALL.reset(token)


def _as_bound(**changed: Any) -> dict[str, Any]:
    return {"data": _BODY, "instance": _ROW, "partial": False} | changed


def test_bound_as_the_validator_binds_it_is_the_one_that_validates(state: EdgeCallState) -> None:
    described = describe_serializer(_In)

    assert bound_description(_In, {"held": _HELD}, **_as_bound()) is described


@pytest.mark.parametrize(
    ("context", "bound"),
    [
        ({"held": _HELD}, _as_bound(data={"title": "a"})),
        ({"held": _HELD}, _as_bound(instance=object())),
        ({"held": _HELD}, _as_bound(partial=True)),
        ({"held": _HELD}, _as_bound(many=True)),
        ({"held": _HELD, "more": 1}, _as_bound()),
        ({"held": object()}, _as_bound()),
    ],
    ids=["another-data", "another-row", "partial", "many", "another-key", "another-object"],
)
def test_bound_another_way_it_is_none(
    state: EdgeCallState, context: dict[str, Any], bound: dict[str, Any]
) -> None:
    describe_serializer(_In)

    assert bound_description(_In, context, **bound) is None


def test_once_it_has_validated_it_is_none(state: EdgeCallState) -> None:
    describe_serializer(_In).is_valid()

    assert bound_description(_In, {"held": _HELD}, **_as_bound()) is None


def test_never_described_it_is_none(state: EdgeCallState) -> None:
    assert bound_description(_In, {"held": _HELD}, **_as_bound()) is None


def test_another_declaration_is_described_for_no_body(state: EdgeCallState) -> None:
    """A serializer a kernel call inside the service describes is no body's and no row's."""
    described = describe_serializer(_Other)

    assert not hasattr(described, "initial_data")
    assert described.instance is None
    assert describe_serializer(_In).initial_data is _BODY
