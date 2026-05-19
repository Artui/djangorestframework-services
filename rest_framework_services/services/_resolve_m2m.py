"""Internal helper: normalise the ``m2m=`` argument of the model service factories.

Each factory accepts ``m2m`` as either:

* a static ``Mapping[str, Any]`` (e.g. ``{"tags": [tag1, tag2]}``), or
* a callable ``(data) -> Mapping[str, Any]`` that derives the mapping from
  the validated input — typical when the M2M values live on the input
  dataclass itself.

Centralising the branch keeps the factory bodies a single line and gives
the type checker a narrow callsite to verify.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast


def resolve_m2m(
    m2m: Mapping[str, Any] | Callable[[Any], Mapping[str, Any]] | None,
    data: Any,
) -> dict[str, Any] | None:
    """Return a plain ``dict`` for ``m2m`` (or ``None`` if not configured).

    The two-arg shape (``Mapping`` *or* ``Callable``) is widened to
    ``object`` and re-narrowed inside the helper so the call-site stays a
    single expression — ``ty`` can't reliably distinguish the two via
    ``isinstance`` alone (a class can in principle be both callable and a
    mapping), so the branches use explicit ``cast`` to record intent.
    """
    if m2m is None:
        return None
    if isinstance(m2m, Mapping):
        return dict(cast("Mapping[str, Any]", m2m))
    derived = cast("Callable[[Any], Mapping[str, Any]]", m2m)(data)
    return dict(derived)
