from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class ProgressReporter(Protocol):
    """How a long-running service reports how far it has got.

    A dispatched callable receives one under the reserved pool seed
    ``progress``, exactly as it receives ``request`` and ``user`` — declare the
    parameter and it arrives::

        def export_invoices(*, data, progress: ProgressReporter):
            rows = list(build_rows(data))
            for index, row in enumerate(rows):
                write(row)
                progress(index + 1, total=len(rows), message="writing rows")

    **Reporting is always safe and never required.** Every transport seeds a
    reporter — the ones with nowhere to send progress seed a no-op — so a
    service that declares the parameter runs unchanged over HTTP, off-HTTP, and
    in tests. The call takes four arguments:

    - ``progress`` — how far along, in whatever unit the service chose. It
      **must increase** across calls within one dispatch; a transport that
      forwards it is entitled to treat a decrease as a bug.
    - ``total`` — the denominator, when it is known. Omit it rather than
      guessing: a receiver renders an indeterminate bar for a missing total
      and a wrong percentage for a wrong one.
    - ``message`` — a short human-readable status. For a person watching, not
      for a machine to parse.
    - ``meta`` — structured detail *about this update* (which stage, which
      file, how many rows have failed), so that structure need not be
      stringified into ``message`` and parsed back out at the far end.

    **``meta`` is the part a receiver may not understand**, and each decides
    for itself: a websocket consumer forwards it into the frame the UI renders;
    a receiver with nowhere to put it drops it. Never encode something the
    operation's *correctness* depends on — it is telemetry, not a channel. And
    namespace the keys if the far end might be MCP: a progress notification
    carries the structure under the protocol's ``_meta``, whose key-naming
    rules reserve unprefixed names and anything under a
    ``modelcontextprotocol`` / ``mcp`` prefix, so ``{"com.example/stage": …}``
    is safe and ``{"stage": …}`` is not portable.

    Implementations must not raise. A reporter is called from inside domain
    code that has no reason to defend against it, and a transport failing
    mid-report should not take the service run down with it.
    """

    def __call__(
        self,
        progress: float,
        *,
        total: float | None = None,
        message: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None: ...


__all__ = ["ProgressReporter"]
