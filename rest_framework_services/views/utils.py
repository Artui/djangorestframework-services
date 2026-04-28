"""Cross-cutting view helpers used by both mutation and query views."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from rest_framework.request import Request


def resolve_extra_kwargs(
    view: Any,
    request: Request,
    *,
    spec_kwargs: Callable[..., dict[str, Any]] | None,
    action_hook: str | None,
    catch_all_hook: str,
) -> dict[str, Any]:
    """Collect the extras that should be merged into a service/selector pool.

    Three layers, applied in order so that the more specific override the
    more general:

    1. ``view.<catch_all_hook>()`` — global fallback declared on the view
       (``get_service_kwargs`` / ``get_selector_kwargs``). No-op when the
       method is not present.
    2. ``view.<action_hook>()`` — per-action method on the view, e.g.
       ``get_create_service_kwargs`` / ``get_list_selector_kwargs``. Skipped
       when ``action_hook`` is ``None`` (e.g. on standalone single-purpose
       views) or the method is absent.
    3. ``spec_kwargs(view, request)`` — per-spec callable from
       :attr:`ServiceSpec.kwargs` / :attr:`SelectorSpec.kwargs`.

    Each layer's result is merged with ``dict.update``, so the spec-level
    provider has the final say on any overlapping keys.
    """
    extras: dict[str, Any] = {}
    catch_all = getattr(view, catch_all_hook, None)
    if catch_all is not None:
        extras.update(catch_all())
    if action_hook is not None:
        hook = getattr(view, action_hook, None)
        if hook is not None:
            extras.update(hook())
    if spec_kwargs is not None:
        extras.update(spec_kwargs(view, request))
    return extras


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
