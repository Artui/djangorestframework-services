from __future__ import annotations

from typing import Any

from rest_framework_services.dispatch.null_progress import null_progress
from rest_framework_services.types.progress_reporter import ProgressReporter


def base_pool(
    *, user: Any, request: Any, progress: ProgressReporter | None = None
) -> dict[str, Any]:
    """The seeds every dispatched callable's pool carries, on every transport.

    **Every** pool — HTTP and off-HTTP both route through here. The two
    view-layer pools used to restate ``request`` / ``user`` inline, which was
    harmless while the set was two names and stopped being harmless the moment
    it grew: a seed added in one place and not the other means a service that
    declares it works over one transport and raises a ``TypeError`` over the
    other.

    ``progress`` defaults to :func:`null_progress` rather than to ``None``, so
    a declared reporter is always callable. See :class:`ProgressReporter` for
    why that default is load-bearing.
    """
    return {"request": request, "user": user, "progress": progress or null_progress}


__all__ = ["base_pool"]
