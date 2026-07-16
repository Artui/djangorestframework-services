"""``resolve_success_status`` — resolve a spec's success status to a concrete code."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from rest_framework_services.views.utils import resolve_callable_kwargs


def resolve_success_status(
    success_status: int | Callable[..., int] | None,
    *,
    default: int,
    pool: Mapping[str, Any],
) -> int:
    """Resolve a :attr:`ServiceSpec.success_status` to an HTTP status code.

    Mirrors the three shapes of the spec field:

    - an ``int`` is returned verbatim;
    - a callable is resolved through the framework keyword ``pool`` — it may
      declare any subset of ``result`` / ``instance`` / ``request`` / ``view``
      (or ``**kwargs``) and receives only what it names — so an upsert can
      return ``200`` for an existing row and ``201`` for a freshly created one;
    - ``None`` falls back to ``default``, the calling surface's
      action-appropriate status (201 create / 200 update / 204 destroy).

    The pool is resolved by the same signature-filtering as every other
    framework callable, so absent keys (e.g. ``instance`` on a bulk path, or
    ``request`` / ``view`` off-HTTP) are simply never passed.
    """
    if success_status is None:
        return default
    if isinstance(success_status, int):
        return success_status
    return success_status(**resolve_callable_kwargs(success_status, dict(pool)))
