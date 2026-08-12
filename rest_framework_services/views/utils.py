"""Cross-cutting view helpers used by both mutation and query views."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any

from rest_framework.request import Request

from rest_framework_services.types.unset import UNSET
from rest_framework_services.types.view_hooks import ViewHooks


def resolve_extra_kwargs(
    view: Any,
    request: Request,
    *,
    spec_kwargs: Callable[..., dict[str, Any]] | None,
    action_hook: str | None,
    catch_all_hook: str,
) -> dict[str, Any]:
    """Collect the extras that should be merged into a service/selector pool.

    Three layers, merged with ``dict.update`` in increasing specificity so the
    spec-level provider has the final say on overlapping keys. Each is invoked
    through the framework's provider convention, receiving only the subset of
    ``{view, request}`` it declares (or the whole pool via ``**kwargs``).

    Args:
        spec_kwargs: The spec's own :attr:`ServiceSpec.kwargs` /
            :attr:`SelectorSpec.kwargs` provider — the most specific layer.
        action_hook: Per-action view method, e.g. ``get_create_service_kwargs``
            / ``get_list_selector_kwargs``. ``None`` (standalone single-purpose
            views) or an absent method skips the layer.
        catch_all_hook: View-wide fallback, e.g. ``get_service_kwargs`` /
            ``get_selector_kwargs``. An absent method skips the layer.
    """
    extras: dict[str, Any] = {}
    catch_all = getattr(view, catch_all_hook, None)
    if catch_all is not None:
        extras.update(_invoke_provider(catch_all, view=view, request=request, extras={}))
    if action_hook is not None:
        hook = getattr(view, action_hook, None)
        if hook is not None:
            extras.update(_invoke_provider(hook, view=view, request=request, extras={}))
    if spec_kwargs is not None:
        extras.update(_invoke_provider(spec_kwargs, view=view, request=request, extras={}))
    return extras


def resolve_input_extras(
    view: Any,
    request: Request,
    *,
    spec_input_data: Callable[..., Mapping[str, Any]] | None,
    action_hook: str | None,
    catch_all_hook: str,
    extras: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect the extras to merge into the serializer input dict.

    :func:`resolve_extra_kwargs`'s layering — ``catch_all_hook`` view method,
    then ``action_hook``, then the spec's :attr:`ServiceSpec.input_data`
    provider — applied to the ``input_serializer``-bound data rather than the
    service-call pool.

    Args:
        extras: The resolved data available before validation — currently the
            mutation target ``instance``, ``None`` on create — offered to each
            provider by keyword only when it declares the name.
    """
    payload: Mapping[str, Any] = extras if extras is not None else {}
    collected: dict[str, Any] = {}
    catch_all = getattr(view, catch_all_hook, None)
    if catch_all is not None:
        collected.update(_invoke_provider(catch_all, view=view, request=request, extras=payload))
    if action_hook is not None:
        hook = getattr(view, action_hook, None)
        if hook is not None:
            collected.update(_invoke_provider(hook, view=view, request=request, extras=payload))
    if spec_input_data is not None:
        collected.update(
            _invoke_provider(spec_input_data, view=view, request=request, extras=payload)
        )
    return collected


def _invoke_provider(
    fn: Callable[..., Any],
    *,
    view: Any,
    request: Request,
    extras: Mapping[str, Any],
) -> Any:
    """Call ``fn`` with the subset of ``{view, request, **extras}`` it declares.

    The single provider-invocation convention: the spec-level ``kwargs`` /
    ``input_data`` / ``*_serializer_context`` callables and the view's ``get_*``
    hooks all come through here, dispatched by :func:`resolve_callable_kwargs`
    exactly as services and selectors are. Bound view-method hooks simply don't
    declare ``view`` (it is their ``self``), so it is filtered out for them.

    A returned key whose value is :data:`~rest_framework_services.UNSET` is
    **dropped** — the provider is declining to set it rather than setting it to
    ``UNSET``. This mirrors the off-HTTP
    :func:`~rest_framework_services.dispatch.utils.resolve_provider` so the
    sentinel means the same thing on every transport.
    """
    pool: dict[str, Any] = {"view": view, "request": request, **extras}
    result: Any = fn(**resolve_callable_kwargs(fn, pool))
    return {key: value for key, value in result.items() if value is not UNSET}


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

    :func:`resolve_serializer_context`'s precedence rules with layer 1 handed in
    rather than read from ``view.get_serializer_context()`` — for overrides of
    that method which need to extend ``super()``'s result without recursing. See
    there for ``action_hook`` / ``spec_provider`` / ``extras``.

    Args:
        base: The layer-1 context to build on.
        direction_hook: Directional view hook name; ``None`` skips the layer.
            That is what ``_ActionSpecsMixin.get_serializer_context`` needs:
            :class:`MutationFlowMixin`'s default
            ``get_output_serializer_context`` would recurse back into
            ``get_serializer_context``.
    """
    payload: Mapping[str, Any] = extras if extras is not None else {}
    context: dict[str, Any] = dict(base)
    if direction_hook is not None:
        direction = getattr(view, direction_hook, None)
        if direction is not None:
            context.update(_invoke_provider(direction, view=view, request=request, extras=payload))
    if action_hook is not None:
        hook = getattr(view, action_hook, None)
        if hook is not None:
            context.update(_invoke_provider(hook, view=view, request=request, extras=payload))
    if spec_provider is not None:
        context.update(_invoke_provider(spec_provider, view=view, request=request, extras=payload))
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

    Four layers, merged with ``dict.update`` in increasing specificity so the
    spec-level provider has the final say on overlapping keys:
    ``view.get_serializer_context()`` (DRF's own) → ``view.<direction_hook>()``
    → ``view.<action_hook>()`` → ``spec_provider``. An absent view method skips
    its layer, so plain DRF viewsets work unchanged.

    Args:
        direction_hook: ``get_input_serializer_context`` /
            ``get_output_serializer_context``.
        action_hook: Per-action override, e.g.
            ``get_create_input_serializer_context``. ``None`` on standalone
            single-purpose views.
        spec_provider: The spec's own ``*_serializer_context`` provider.
        extras: The resolved data about to be serialized (a mutation's
            ``result``, the retrieved ``instance``, or the list ``page``),
            offered to the last three layers by keyword only when declared.
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

    A function stored as a plain class attribute (``service = my_fn``) would
    otherwise be wrapped in a bound method when read via ``self``.
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


def resolve_progress_hook(view: Any, request: Request, *, action: str | None) -> Any:
    """First progress reporter the view offers: per-action hook, then catch-all.

    Most-specific wins and there is **no merging** — a reporter is a single sink,
    not a set of keys, so layering two of them would mean silently fanning out;
    a view that wants that composes them itself with
    :func:`~rest_framework_services.combine_progress`. ``None`` when the view
    offers neither, which leaves the seed as
    :func:`~rest_framework_services.null_progress`.
    """
    for name in (f"get_{action}_progress_reporter" if action else None, "get_progress_reporter"):
        hook = getattr(view, name, None) if name else None
        if hook is not None:
            reporter = hook(**resolve_callable_kwargs(hook, {"view": view, "request": request}))
            if reporter is not None:
                return reporter
    return None


# Lives here rather than beside the mutation flow because the selector path
# needs it too, and ``views.mutation.utils`` imports ``selectors.utils`` —
# putting it there would make the dependency circular.
def resolve_view_hooks(
    view: Any,
    request: Request,
    *,
    chain: str = "service",
    instance: Any = None,
) -> ViewHooks:
    """Resolve the calling view's hook chains into a :class:`ViewHooks` carrier.

    **View layers only** — every ``spec_*`` argument below is deliberately
    ``None``. The chains run ``view.get_<x>`` → ``view.get_<action>_<x>`` →
    ``spec.<x>``, and ``dispatch_spec`` owns that last layer; resolving the spec
    provider here too would invoke it **twice**, which is not safe for a provider
    that queries the database. See :class:`ViewHooks`.

    Args:
        chain: Which kwargs chain to collect — ``"service"`` for mutations,
            ``"selector"`` for reads. Only the view-method names differ, which is
            why one carrier serves both. The input-phase fields are mutation-only
            and stay unset for a selector: a read has no payload to merge into
            and no input serializer to give context to.
        instance: The resolved mutation target (``None`` on create and on every
            bulk path), offered to the ``input_data`` providers that declare it.
    """
    action: str | None = getattr(view, "action", None)
    extra_kwargs = resolve_extra_kwargs(
        view,
        request,
        spec_kwargs=None,
        action_hook=f"get_{action}_{chain}_kwargs" if action else None,
        catch_all_hook=f"get_{chain}_kwargs",
    )
    progress = resolve_progress_hook(view, request, action=action)
    if chain != "service":
        return ViewHooks(extra_kwargs=extra_kwargs, progress=progress)
    return ViewHooks(
        extra_kwargs=extra_kwargs,
        progress=progress,
        input_data=resolve_input_extras(
            view,
            request,
            spec_input_data=None,
            action_hook=f"get_{action}_input_data" if action else None,
            catch_all_hook="get_input_data",
            extras={"instance": instance},
        ),
        input_serializer_context=layer_serializer_context(
            {},
            view,
            request,
            direction_hook="get_input_serializer_context",
            action_hook=f"get_{action}_input_serializer_context" if action else None,
        ),
        output_serializer_context=lambda result: layer_serializer_context(
            {},
            view,
            request,
            direction_hook="get_output_serializer_context",
            action_hook=f"get_{action}_output_serializer_context" if action else None,
            extras={"result": result},
        ),
    )
