"""Tests for ``combine_progress``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.dispatch.combine_progress import combine_progress
from rest_framework_services.dispatch.null_progress import null_progress


def _recorder(into: list[tuple[Any, ...]]) -> Any:
    def report(
        progress: float,
        *,
        total: float | None = None,
        message: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        into.append((progress, total, message, meta))

    return report


def test_no_reporters_returns_the_no_op() -> None:
    # The seed must stay callable whatever the caller passed, or a service that
    # declares ``progress`` breaks on the transport that supplies nothing.
    assert combine_progress() is null_progress
    assert combine_progress(None, None) is null_progress


def test_a_single_reporter_is_returned_unwrapped() -> None:
    seen: list[tuple[Any, ...]] = []
    only = _recorder(seen)
    assert combine_progress(None, only) is only


def test_forwards_every_argument_to_every_sink() -> None:
    a: list[tuple[Any, ...]] = []
    b: list[tuple[Any, ...]] = []
    combine_progress(_recorder(a), _recorder(b))(3, total=10, message="hi", meta={"x/y": 1})
    assert a == [(3, 10, "hi", {"x/y": 1})]
    assert a == b


def test_a_failing_sink_does_not_silence_the_ones_after_it() -> None:
    # The reason this is a helper rather than a loop. A naive fan-out would let
    # a broken audit writer take the MCP notifications behind it down with it.
    seen: list[tuple[Any, ...]] = []

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("sink is down")

    combine_progress(boom, _recorder(seen))(1)
    assert seen == [(1, None, None, None)]


def test_a_failing_sink_does_not_escape_into_the_caller() -> None:
    # A reporter is called from domain code that has no reason to defend against
    # it; a telemetry failure must never take down the operation it describes.
    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("sink is down")

    combine_progress(boom, boom)(1)  # must not raise


def test_order_is_preserved() -> None:
    order: list[str] = []
    first = lambda *_, **__: order.append("first")  # noqa: E731
    second = lambda *_, **__: order.append("second")  # noqa: E731
    combine_progress(first, second)(1)
    assert order == ["first", "second"]
