"""``combine_progress`` — fan one progress report out to several reporters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.dispatch.null_progress import null_progress
from rest_framework_services.types.progress_reporter import ProgressReporter


def combine_progress(*reporters: ProgressReporter | None) -> ProgressReporter:
    """Return one :class:`ProgressReporter` that forwards to all of ``reporters``.

    The merge point for the two kinds of progress sink a dispatch can have: the
    **transport-native** one the caller supplies (MCP notifications, a websocket
    the view wired up) and the **transport-independent** one a spec declares
    (a task record, an audit trail, metrics). Both are legitimate, they are
    chosen by different people for different reasons, and neither should
    displace the other — so the core fans out rather than picking.

    ``None`` entries are dropped, so a caller can pass an optional reporter
    straight through. With nothing left, :func:`null_progress` comes back —
    the seed stays callable, which is the invariant every service relies on.

    **Per-sink isolation is the whole reason this is a helper.** The obvious
    loop::

        for reporter in reporters:
            reporter(progress, total=total, message=message, meta=meta)

    breaks the Protocol's standing promise that *implementations must not
    raise*. One sink throwing skips every sink after it — so a failing audit
    writer silently costs you the MCP notifications behind it — and the
    exception lands in domain code that has no reason to defend against a
    telemetry call, taking the run down with it. A reporter that kills the
    operation it was only supposed to describe is worse than one that reports
    nothing. Each sink is therefore isolated: it gets its report, and its
    failure is its own.

    **Failures are swallowed, not logged, and that is a real trade.** This
    module cannot know which logger a consumer would want, and reaching for one
    from inside a fan-out that runs per progress tick invites a second failure
    mode on the path meant to be inert. A sink that cares about its own errors
    handles them where it can name them — which is inside the sink, where the
    context to act on them exists.

    Order is preserved, so a caller that wants its own sink to see a report
    first passes it first — but no sink may *depend* on that, since none of them
    can observe whether an earlier one succeeded.
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
            except Exception:  # noqa: BLE001 — a sink's failure is its own; see above.
                continue

    return report


__all__ = ["combine_progress"]
