"""``provider_keys`` -- the keys a ``kwargs=`` provider declares, read before it runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, get_args, get_origin, get_type_hints

from rest_framework_services.types.unset import UnsetType


def provider_keys(
    provider: Callable[..., Any] | None,
) -> tuple[frozenset[str], frozenset[str]] | None:
    """The names ``provider`` fills and those it may decline, or ``None`` if unknown.

    The reader both spec transports carried a copy of, moved here unchanged.
    """
    if provider is None:
        return frozenset(), frozenset()
    try:
        returned: Any = get_type_hints(provider).get("return")
        declared: Any = get_origin(returned) or returned
        if getattr(declared, "__required_keys__", None) is None:
            return None
        values: dict[str, Any] = get_type_hints(declared)
    except Exception:
        return None
    names = frozenset(declared.__required_keys__) | frozenset(declared.__optional_keys__)
    declinable = frozenset(name for name in names if _admits_unset(values.get(name)))
    return names - declinable, declinable


def _admits_unset(annotation: Any) -> bool:
    return annotation is UnsetType or any(_admits_unset(arg) for arg in get_args(annotation))


__all__ = ["provider_keys"]
