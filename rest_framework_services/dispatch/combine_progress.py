"""``combine_progress`` — fan one progress report out to several reporters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.dispatch.null_progress import null_progress
from rest_framework_services.types.progress_reporter import ProgressReporter


def combine_progress(*reporters: ProgressReporter | None) -> ProgressReporter:
    """Return one :class:`ProgressReporter` that forwards to all of ``reporters``.

    The merge point for the two kinds of sink a dispatch can have: the
    **transport-native** one the caller supplies (MCP notifications, a websocket
    the view wired up) and the **transport-independent** one a spec declares
    (a task record, an audit trail, metrics). Neither displaces the other — the
    core fans out rather than picking.

    Each sink is isolated: it gets its report, and its failure is its own, so a
    throwing sink neither skips the sinks behind it nor takes down the run it
    was only meant to describe. Failures are **swallowed, not logged** — a sink
    that cares about its own errors handles them inside itself, where the
    context to act on them exists.

    Args:
        *reporters: The sinks to fan out to, in call order. ``None`` entries are
            dropped, so an optional reporter can be passed straight through.

    Returns:
        A reporter forwarding to every non-``None`` sink, in the order given;
        :func:`null_progress` when none are left, so the pool seed stays
        callable. No sink may *depend* on the order — none can observe whether
        an earlier one succeeded.
    """
    sinks: tuple[ProgressReporter, ...] = tuple(r for r in reporters if r is not None)
    if not sinks:
        return null_progress
    if len(sinks) == 1:
        return sinks[0]

    def report(
        progress: float,
        *,
        total: float | None = None,
        message: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        for sink in sinks:
            try:
                sink(progress, total=total, message=message, meta=meta)
            except Exception:  # noqa: BLE001 — a sink's failure must not skip the rest.
                continue

    return report


__all__ = ["combine_progress"]
