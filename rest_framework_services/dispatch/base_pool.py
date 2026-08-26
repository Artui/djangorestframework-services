from __future__ import annotations

from typing import Any

from rest_framework_services.dispatch.null_progress import null_progress
from rest_framework_services.types.progress_reporter import ProgressReporter


def base_pool(
    *, user: Any, request: Any, progress: ProgressReporter | None = None, **extra: Any
) -> dict[str, Any]:
    """The seeds a dispatched callable's kwargs pool carries, on every transport.

    Every pool this package builds for a dispatched spec routes through here — the
    HTTP view layer and off-HTTP
    [`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec]
    alike — so a spec callable that declares a seed behaves the same whichever of
    them dispatched it.

    That is a property of the pools built here, not a rule the framework can
    enforce on its callers: this is a builder, not a gate. A transport adapter that
    assembles a pool as a dict literal of its own carries exactly the keys it wrote
    there, because ``resolve_callable_kwargs`` forwards only keys the pool actually
    has. A callable declaring a seed the literal omitted then raises ``TypeError``
    at call time rather than running — ``progress`` most often, since it is the seed
    with a default and so the one nobody remembers.

    **An adapter that dispatches callables through this package must build its pool
    from this function**, with its own entries spread in —
    ``base_pool(user=…, request=…, **own_entries)`` — rather than restating the
    seeds. Routing those entries through ``**extra`` is also what makes a name
    collision loud: an entry called ``user`` or ``request`` raises ``TypeError``
    here, where in a dict literal it would quietly outrank the value the transport
    authenticated.

    ``progress`` defaults to
    [`null_progress`][rest_framework_services.dispatch.null_progress.null_progress]
    rather than to ``None``, so a declared reporter is always callable — see
    [`ProgressReporter`][rest_framework_services.types.progress_reporter.ProgressReporter].
    """
    return {"request": request, "user": user, "progress": progress or null_progress, **extra}


__all__ = ["base_pool"]
