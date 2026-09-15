from __future__ import annotations

from typing import Any

from rest_framework_services.dispatch.null_progress import null_progress
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS, PoolSeeds
from rest_framework_services.types.progress_reporter import ProgressReporter

# The declare-to-receive helper every spec callable is resolved through. A seed's
# resolver is one of those callables, so it binds by the same rule rather than a
# second one. (``dispatch/utils.py`` already reaches into ``views/`` for this;
# the module is request-free despite its home.)
from rest_framework_services.views.utils import resolve_callable_kwargs


def base_pool(
    *,
    user: Any,
    request: Any,
    progress: ProgressReporter | None = None,
    seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
    **extra: Any,
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

    ``seeds`` is the project's own registry
    ([`PoolSeeds`][rest_framework_services.types.pool_seeds.PoolSeeds]), resolved
    into every pool this builds. It is the **supported** way to add an entry, and
    the difference from spreading one through ``**extra`` is not convenience: a
    registered name is also reserved, so client input cannot occupy it, and is
    exempt from unknown-argument accounting. An ``**extra`` entry gets neither —
    which on a selector, where no validator stands in front of the spread, means
    a caller can supply it. Spread an entry that is genuinely per-call; register
    one that is ambient.
    """
    pool: dict[str, Any] = {
        "request": request,
        "user": user,
        "progress": progress or null_progress,
        **extra,
    }
    # Every resolver binds against the pool *before* any seed is written, so the
    # result does not depend on registration order and a reader does not have to
    # know it. A seed that needs another seed's value composes the two in its own
    # resolver, which keeps that dependency written down where it applies.
    bound = dict(pool)
    for name, resolver in seeds.resolvers().items():
        if name in bound:
            raise TypeError(
                f"base_pool() got a registered pool seed {name!r} that an entry already "
                f"occupies. A seed cannot silently outrank what the caller spread in."
            )
        pool[name] = resolver(**resolve_callable_kwargs(resolver, bound))
    return pool


__all__ = ["base_pool"]
