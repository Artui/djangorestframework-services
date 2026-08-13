from __future__ import annotations

from typing import Any

from rest_framework_services.dispatch.null_progress import null_progress
from rest_framework_services.types.progress_reporter import ProgressReporter


def base_pool(
    *, user: Any, request: Any, progress: ProgressReporter | None = None
) -> dict[str, Any]:
    """The seeds every dispatched callable's pool carries, on every transport.

    **Every** pool routes through here, HTTP and off-HTTP alike, so a service
    that declares a seed behaves the same whoever dispatched it. Build a pool
    of your own from this rather than restating the seeds.

    ``progress`` defaults to [`null_progress`][rest_framework_services.dispatch.null_progress.null_progress] rather than to ``None``, so
    a declared reporter is always callable — see [`ProgressReporter`][rest_framework_services.types.progress_reporter.ProgressReporter].
    """
    return {"request": request, "user": user, "progress": progress or null_progress}


__all__ = ["base_pool"]
