from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def null_progress(
    progress: float,
    *,
    total: float | None = None,
    message: str | None = None,
    meta: Mapping[str, Any] | None = None,
) -> None:
    """The :class:`ProgressReporter` a transport with nowhere to send progress uses.

    Seeded by :func:`base_pool` whenever the caller supplied no reporter, which
    is every HTTP request, every test, and every off-HTTP dispatch from a
    transport that has no progress channel of its own.

    **The default is what makes the seed usable.** Without it, a service
    declaring ``progress`` would work over one transport and raise a
    ``TypeError`` over the others, so nobody could declare it in code meant to
    be shared — which is the entire premise of writing a service once. Discarding
    the report is the honest behaviour for a caller that cannot forward it;
    refusing the call is not.
    """
    del progress, total, message, meta


__all__ = ["null_progress"]
