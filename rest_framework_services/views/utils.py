"""Cross-cutting view helpers used by both mutation and query views."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
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


def resolve_input_extras(
    view: Any,
    request: Request,
    *,
    spec_input_data: Callable[..., Mapping[str, Any]] | None,
    action_hook: str | None,
    catch_all_hook: str,
) -> dict[str, Any]:
    """Collect the extras to merge into the serializer input dict.

    Mirrors :func:`resolve_extra_kwargs` but for the
    ``input_serializer``-bound data, not the service-call pool. Layers,
    applied in order of increasing specificity (later wins on overlap):

    1. ``view.<catch_all_hook>(request)`` — global fallback
       (``get_input_data``); typically returns ``{}``.
    2. ``view.<action_hook>(request)`` — per-action method on the view
       (``get_<action>_input_data``). Skipped when ``action_hook`` is
       ``None`` (standalone single-purpose views) or the method is absent.
    3. ``spec_input_data(view, request)`` — per-spec callable from
       :attr:`ServiceSpec.input_data`.

    Each layer's result is merged with ``dict.update`` so the spec-level
    provider has the final say on overlapping keys.
    """
    extras: dict[str, Any] = {}
    catch_all = getattr(view, catch_all_hook, None)
    if catch_all is not None:
        extras.update(catch_all(request))
    if action_hook is not None:
        hook = getattr(view, action_hook, None)
        if hook is not None:
            extras.update(hook(request))
    if spec_input_data is not None:
        extras.update(spec_input_data(view, request))
    return extras


def _invoke_with_extras(
    fn: Callable[..., Any],
    *leading: Any,
    extras: Mapping[str, Any],
) -> Any:
    """Call ``fn(*leading, **declared)`` passing only the extras it declares.

    ``leading`` is forwarded positionally and unconditionally (the
    ``view, request`` pair for spec providers, nothing for bound view-method
    hooks). Each entry in ``extras`` (the resolved data — ``result`` /
    ``instance`` / ``page``) is passed by keyword **only** when ``fn``
    declares a parameter of that name or accepts ``**kwargs``.

    This is what keeps the widening backward compatible: a legacy
    ``(view, request)`` provider declares neither extra, so it is called as
    ``fn(view, request)`` exactly as before — regardless of how it names
    those two positional parameters. Mirrors :func:`resolve_callable_kwargs`'s
    "pass only what you declare" rule for the context-provider call sites.
    """
    params = inspect.signature(fn).parameters
    accepts_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    declared: dict[str, Any] = {
        name: value for name, value in extras.items() if accepts_var_keyword or name in params
    }
    return fn(*leading, **declared)


def layer_serializer_context(
    base: Mapping[str, Any],
    view: Any,
    request: Request,
    *,
    direction_hook: str | None,
    action_hook: str | None,
    spec_provider: Callable[..., Mapping[str, Any]] | None = None,
    extras: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Layer the directional, action, and spec context hooks onto ``base``.

    Same precedence rules as :func:`resolve_serializer_context`, but takes
    the layer-1 dict explicitly instead of calling ``view.get_serializer_context()``.
    Used by ``get_serializer_context()`` overrides that need to extend
    ``super().get_serializer_context()`` without recursing.

    ``direction_hook=None`` skips the directional layer entirely. The
    canonical use of this is ``_ActionSpecsMixin.get_serializer_context``,
    which can't safely call ``get_output_serializer_context`` because the
    default implementation on :class:`MutationFlowMixin` would recurse
    back into ``get_serializer_context``.

    ``extras`` carries the resolved data about to be serialized (the
    ``result`` of a mutation, the retrieved ``instance``, or the list
    ``page``). Each provider receives only the extras it declares by name
    (see :func:`_invoke_with_extras`); legacy ``(view, request)`` providers
    are unaffected. ``None`` is treated as an empty mapping.
    """
    payload: Mapping[str, Any] = extras if extras is not None else {}
    context: dict[str, Any] = dict(base)
    if direction_hook is not None:
        direction = getattr(view, direction_hook, None)
        if direction is not None:
            context.update(_invoke_with_extras(direction, extras=payload))
    if action_hook is not None:
        hook = getattr(view, action_hook, None)
        if hook is not None:
            context.update(_invoke_with_extras(hook, extras=payload))
    if spec_provider is not None:
        context.update(_invoke_with_extras(spec_provider, view, request, extras=payload))
    return context


def resolve_serializer_context(
    view: Any,
    request: Request,
    *,
    direction_hook: str,
    action_hook: str | None,
    spec_provider: Callable[..., Mapping[str, Any]] | None = None,
    extras: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the serializer context dict for one direction (input or output).

    Four layers, applied in order so that the more specific override the
    more general:

    1. ``view.get_serializer_context()`` — DRF's default, available on every
       :class:`~rest_framework.generics.GenericAPIView` (returns ``request``,
       ``view``, ``format``).
    2. ``view.<direction_hook>()`` — library directional fallback
       (``get_input_serializer_context`` / ``get_output_serializer_context``).
       Skipped when the method is absent, so plain DRF viewsets work unchanged.
    3. ``view.<action_hook>()`` — per-action override on the view, e.g.
       ``get_create_input_serializer_context`` /
       ``get_list_output_serializer_context``. Skipped when ``action_hook``
       is ``None`` (standalone single-purpose views) or the method is absent.
    4. ``spec_provider(view, request)`` — per-spec callable from
       :attr:`ServiceSpec.input_serializer_context` /
       :attr:`ServiceSpec.output_serializer_context` /
       :attr:`SelectorSpec.output_serializer_context`. Skipped when ``None``.

    Each layer's result is merged with ``dict.update``, so the spec-level
    provider has the final say on overlapping keys.

    ``extras`` (the resolved data — ``result`` / ``instance`` / ``page``)
    is offered to the directional, action, and spec providers by keyword,
    each receiving only the names it declares. See
    :func:`layer_serializer_context`.
    """
    return layer_serializer_context(
        view.get_serializer_context(),
        view,
        request,
        direction_hook=direction_hook,
        action_hook=action_hook,
        spec_provider=spec_provider,
        extras=extras,
    )


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
