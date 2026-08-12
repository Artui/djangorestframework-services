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
    in tests. That is the whole point of putting it in the pool rather than
    passing it as an argument only some callers know about: the service is
    written once, and whether anyone is listening is the transport's business.

    The four arguments split into "what every receiver understands" and "what
    only yours does":

    - ``progress`` — how far along, in whatever unit the service chose. It
      **must increase** across calls within one dispatch; a transport that
      forwards it is entitled to treat a decrease as a bug.
    - ``total`` — the denominator, when it is known. Omit it rather than
      guessing: a receiver renders an indeterminate bar for a missing total
      and a wrong percentage for a wrong one.
    - ``message`` — a short human-readable status. For a person watching, not
      for a machine to parse.
    - ``meta`` — structured detail *about this update*, for a receiver that
      knows what to do with it.

    **``meta`` is what keeps the first three honest.** Without it, a service
    with structured state to report — which stage it is in, which file it is
    on, how many rows failed so far — has to stringify it into ``message`` and
    have the far end parse it back out. That is a wire format invented by
    accident, in a string field documented as being for humans. Put the
    structure in ``meta`` and leave ``message`` as prose::

        progress(
            processed,
            total=row_count,
            message=f"importing {path.name}",
            meta={"stage": "import", "file": path.name, "failed": failures},
        )

    **``meta`` is the part a receiver may not understand**, and each decides
    for itself: a websocket consumer forwards it into the frame the UI renders;
    a receiver with nowhere to put it drops it. Never encode something the
    operation's *correctness* depends on — it is telemetry, not a channel.

    **Namespace the keys if the far end might be MCP.** A progress
    notification carries the structure under the protocol's ``_meta``, whose
    key-naming rules reserve unprefixed names and anything under a
    ``modelcontextprotocol`` / ``mcp`` prefix. ``{"com.example/stage": …}`` is
    safe; ``{"stage": …}`` is not portable.

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
