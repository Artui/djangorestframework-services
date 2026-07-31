from __future__ import annotations

from typing import Protocol


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

    ⚠ **Reporting is always safe and never required.** Every transport seeds a
    reporter — the ones with nowhere to send progress seed a no-op — so a
    service that declares the parameter runs unchanged over HTTP, off-HTTP, and
    in tests. That is the whole point of putting it in the pool rather than
    passing it as an argument only some callers know about: the service is
    written once, and whether anyone is listening is the transport's business.

    The shape mirrors what a progress-carrying wire protocol needs:

    - ``progress`` — how far along, in whatever unit the service chose. It
      **must increase** across calls within one dispatch; a transport that
      forwards it is entitled to treat a decrease as a bug.
    - ``total`` — the denominator, when it is known. Omit it rather than
      guessing: a receiver renders an indeterminate bar for a missing total
      and a wrong percentage for a wrong one.
    - ``message`` — a short human-readable status. For a person watching, not
      for a machine to parse.

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
    ) -> None: ...


__all__ = ["ProgressReporter"]
