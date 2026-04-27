"""Cross-cutting view helpers used by both mutation and query views."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any


def get_class_attr(view: Any, name: str) -> Any:
    """Return the named class attribute without instance binding.

    Functions stored as plain class attributes (e.g. ``service = my_fn``)
    would otherwise be wrapped in a bound method when accessed via ``self``.
    Use this helper to retrieve them as the original callable.
    """
    return getattr(type(view), name, None)


def resolve_callable_kwargs(
    fn: Callable[..., Any],
    pool: dict[str, Any],
) -> dict[str, Any]:
    """Pick the subset of ``pool`` matching ``fn``'s declared parameters.

    If ``fn`` declares ``**kwargs``, the entire pool is passed.
    Otherwise only parameters present in the signature are forwarded.
    """
    signature: inspect.Signature = inspect.signature(fn)
    params: dict[str, inspect.Parameter] = dict(signature.parameters)

    accepts_var_keyword: bool = any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
    )
    if accepts_var_keyword:
        return dict(pool)

    declared_names: set[str] = {
        name
        for name, param in params.items()
        if param.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    return {name: pool[name] for name in declared_names if name in pool}
