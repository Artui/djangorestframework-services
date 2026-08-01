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

    Three layers, applied in order so that the more specific override the
    more general:

    1. ``view.<catch_all_hook>()`` — global fallback declared on the view
       (``get_service_kwargs`` / ``get_selector_kwargs``). No-op when the
       method is not present.
    2. ``view.<action_hook>()`` — per-action method on the view, e.g.
       ``get_create_service_kwargs`` / ``get_list_selector_kwargs``. Skipped
       when ``action_hook`` is ``None`` (e.g. on standalone single-purpose
       views) or the method is absent.
    3. ``spec_kwargs`` — per-spec callable from :attr:`ServiceSpec.kwargs` /
       :attr:`SelectorSpec.kwargs`.

    Every layer is invoked through :func:`_invoke_provider`, so each callable
    receives only the subset of ``{view, request}`` it declares (or the whole
    pool via ``**kwargs``) — declare just ``view``, just ``request``, both, or
    neither. Each layer's result is merged with ``dict.update``, so the
    spec-level provider has the final say on any overlapping keys.
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

    Mirrors :func:`resolve_extra_kwargs` but for the
    ``input_serializer``-bound data, not the service-call pool. Layers,
    applied in order of increasing specificity (later wins on overlap):

    1. ``view.<catch_all_hook>`` — global fallback (``get_input_data``);
       typically returns ``{}``.
    2. ``view.<action_hook>`` — per-action method on the view
       (``get_<action>_input_data``). Skipped when ``action_hook`` is
       ``None`` (standalone single-purpose views) or the method is absent.
    3. ``spec_input_data`` — per-spec callable from
       :attr:`ServiceSpec.input_data`.

    Each layer's result is merged with ``dict.update`` so the spec-level
    provider has the final say on overlapping keys.

    Every layer is invoked through :func:`_invoke_provider`, so a provider
    declares only what it needs from ``{view, request}`` plus ``extras`` (or
    ``**kwargs``). ``extras`` carries the resolved data available before
    validation — currently the mutation target ``instance`` (``None`` on
    create) — offered by keyword only when declared.
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

    The single provider-invocation convention for the framework. Every
    provider — the spec-level ``kwargs`` / ``input_data`` /
    ``*_serializer_context`` callables **and** the view's ``get_*`` hooks — is
    dispatched through :func:`resolve_callable_kwargs` against a pool of
    ``view`` / ``request`` plus the resolved-data ``extras`` (``result`` /
    ``instance`` / ``page``). A provider declares only what it needs — just
    ``view``, just ``request``, any subset of the extras, both, neither, or
    ``**kwargs`` — exactly as services and selectors are dispatched. Bound
    view-method hooks simply don't declare ``view`` (it is their ``self``), so
    it is filtered out for them.

    A returned key whose value is :data:`~rest_framework_services.UNSET` is
    **dropped** — the provider is declining to set it, not setting it to
    ``UNSET``. This mirrors the off-HTTP
    :func:`~rest_framework_services.dispatch.utils.resolve_provider` so the
    sentinel means the same thing on every transport: a provider that can't (or
    shouldn't) resolve a key steps aside rather than overriding a value supplied
    elsewhere.
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
    ``page``). Every layer is invoked through :func:`_invoke_provider`, so a
    provider receives only the subset of ``{view, request, **extras}`` it
    declares (or the whole pool via ``**kwargs``). ``None`` is treated as an
    empty mapping.
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


def resolve_view_hooks(
    view: Any,
    request: Request,
    *,
    chain: str = "service",
    instance: Any = None,
) -> ViewHooks:
    """Resolve the calling view's hook chains into a :class:`ViewHooks` carrier.

    ⚠ **View layers only** — every ``spec_*`` argument below is deliberately
    ``None``. The chains run ``view.get_<x>`` → ``view.get_<action>_<x>`` →
    ``spec.<x>``, and ``dispatch_spec`` owns that last layer. Resolving the spec
    provider here too would invoke it **twice**, which is not safe for a provider
    that queries the database. See :class:`ViewHooks`.

    ``chain`` selects which kwargs chain to collect: ``"service"``
    (``get_service_kwargs`` / ``get_<action>_service_kwargs``) for mutations,
    ``"selector"`` (``get_selector_kwargs`` / ``get_<action>_selector_kwargs``)
    for reads. Only the view-method names differ — the layering is identical,
    which is why one carrier serves both.

    The input-phase fields are mutation-only and stay unset for a selector: a
    read has no payload to merge into and no input serializer to give context to,
    so populating them would invite a reader to think it does.

    ``instance`` (the resolved mutation target, ``None`` on create and on every
    bulk path) is offered to the ``input_data`` providers that declare it.

    Lives here rather than beside the mutation flow because the selector path
    needs it too, and ``views.mutation.utils`` imports ``selectors.utils`` —
    putting it there would make the dependency circular.
    """
    action: str | None = getattr(view, "action", None)
    extra_kwargs = resolve_extra_kwargs(
        view,
        request,
        spec_kwargs=None,
        action_hook=f"get_{action}_{chain}_kwargs" if action else None,
        catch_all_hook=f"get_{chain}_kwargs",
    )
    if chain != "service":
        return ViewHooks(extra_kwargs=extra_kwargs)
    return ViewHooks(
        extra_kwargs=extra_kwargs,
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
