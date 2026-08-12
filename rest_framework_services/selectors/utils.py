"""Internal selector dispatch helpers (sync + async)."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model, QuerySet
from django.db.models.manager import BaseManager
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request

from rest_framework_services.is_async import is_async
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.views.utils import resolve_view_hooks


def is_queryset(obj: Any) -> bool:
    """True for Django ``QuerySet`` objects and ``Manager`` instances.

    These are the queryset-shaping targets: the things the four shaping
    fields can be applied to, and the things a RETRIEVE selector / output
    selector should be materialized from via ``.first()``. Centralizes the
    "is this a queryset?" decision so the selector and mutation dispatch
    paths agree on one definition instead of duck-typing on a method name
    (``hasattr(..., "first")``), which would also match an unrelated domain
    object that happens to expose ``first``. ``QuerySet`` subclasses
    (``.values()``, ``.values_list()``, polymorphic querysets, …) all pass.
    """
    return isinstance(obj, (QuerySet, BaseManager))


def run_selector(fn: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    """Call a selector from sync code, transparently bridging async ones."""
    if is_async(fn):
        return async_to_sync(fn)(**kwargs)
    return fn(**kwargs)


async def arun_selector(
    fn: Callable[..., Any] | Callable[..., Awaitable[Any]],
    kwargs: dict[str, Any],
) -> Any:
    """Call a selector from async code; sync ones run inline."""
    if is_async(fn):
        return await fn(**kwargs)
    return fn(**kwargs)


def apply_queryset_shaping(
    qs: Any,
    view: Any,
    request: Request,
    *,
    select_related: Any,
    prefetch_related: Any,
    annotations: Any,
    extend_queryset: Any,
    filter_set: Any = None,
    filter_data: Any = None,
    source_label: str,
) -> Any:
    """Apply the five shaping fields to ``qs``.

    Declarative fields apply first (in declaration order), then
    ``extend_queryset`` runs so the user callable always sees the fully
    statically-shaped queryset, and finally ``filter_set`` narrows it via the
    transport-neutral ``filter_set(data=filter_data, queryset=qs).qs`` contract
    — validated first (see :func:`_raise_on_invalid_filter`, mirroring
    ``DjangoFilterBackend``'s 400-on-invalid-filter) — so filtering composes with
    shaping and runs before the retrieve ``.first()`` materialization the caller
    does next. Returns ``qs`` unchanged when no shaping is configured.

    Raises :exc:`ImproperlyConfigured` when shaping is configured but
    ``qs`` is not a Django QuerySet (no ``annotate`` method) — loud
    failure beats a stale ``AttributeError`` deep in DRF rendering.
    ``source_label`` is included in the error to point at the misuse
    (``"SelectorSpec.selector"`` vs ``"ServiceSpec.output_selector_spec.selector"``).

    ``filter_set`` defaults to ``None`` so existing callers of this blessed
    surface keep working unchanged. ``filter_data`` is the data the FilterSet
    reads (a flat ``{field: value}`` mapping); it defaults to ``None``, in
    which case the value falls back to ``request.query_params`` — the HTTP view
    path. A transport-neutral caller (``dispatch_spec``) passes its own params
    here. ``request`` is forwarded into the FilterSet when its constructor
    declares it (see :func:`_filter_set_accepts_request`), so a request-scoped
    ``FilterSet`` sees the same ``self.request`` it would behind
    ``DjangoFilterBackend`` instead of ``None`` — real on the HTTP / MCP paths, a
    faithful-``user`` / -``query_params`` synthetic off-HTTP; a bare
    ``(data, queryset)`` stand-in that doesn't declare ``request`` is unaffected.
    """
    if (
        select_related is None
        and prefetch_related is None
        and annotations is None
        and extend_queryset is None
        and filter_set is None
    ):
        return qs
    if not is_queryset(qs):
        raise ImproperlyConfigured(
            "select_related / prefetch_related / annotations / extend_queryset "
            f"/ filter_set are set on the spec but {source_label} returned "
            f"{type(qs).__name__}, which is not a Django QuerySet. Drop the "
            "shaping fields or have the callable return a QuerySet."
        )
    if select_related is not None:
        qs = qs.select_related(*select_related)
    if prefetch_related is not None:
        qs = qs.prefetch_related(*prefetch_related)
    if annotations is not None:
        qs = qs.annotate(**annotations)
    if extend_queryset is not None:
        qs = extend_queryset(qs, view, request)
    if filter_set is not None:
        data = filter_data if filter_data is not None else request.query_params
        call_kwargs: dict[str, Any] = {"data": data, "queryset": qs}
        if _filter_set_accepts_request(filter_set):
            call_kwargs["request"] = request
        bound = filter_set(**call_kwargs)
        _raise_on_invalid_filter(bound)
        qs = bound.qs
    return qs


def _raise_on_invalid_filter(filterset: Any) -> None:
    """Reject invalid filter input the way DRF's ``DjangoFilterBackend`` does.

    A django-filter ``FilterSet`` validates its bound form via ``is_valid()``
    and exposes the failures on ``.errors``. Reading ``.qs`` *without* validating
    silently returns the **unfiltered** queryset in django-filter's default
    non-strict mode — so a bad ``?field=`` value (e.g. a ``ChoiceFilter`` value
    outside its choices) would answer 200 with unfiltered rows instead of the
    400 ``DjangoFilterBackend`` gives by default. ``filter_set`` replaces that
    backend on the list path, so it must keep the same contract.

    Only enforced when the duck-typed ``filter_set`` actually exposes
    ``is_valid`` — a bare ``(data, queryset) -> .qs`` stand-in that doesn't opt
    into validation keeps its pass-through behaviour. The DRF ``ValidationError``
    is built straight from ``filterset.errors`` (a Django form ``ErrorDict``,
    which DRF renders into the same ``{field: [msg]}`` 400 shape) so the core
    never has to import django-filter — the reason ``filter_set`` is duck-typed
    in the first place.
    """
    is_valid = getattr(filterset, "is_valid", None)
    if is_valid is not None and not is_valid():
        raise ValidationError(filterset.errors)


def _filter_set_accepts_request(filter_set: Any) -> bool:
    """True when ``filter_set``'s constructor declares a ``request`` parameter.

    ``filter_set`` is applied by duck typing — the blessed contract is only
    ``(data, queryset) -> .qs`` (so ``types/`` and the package import nothing). A
    ``django-filter`` ``FilterSet`` *also* takes ``request`` on its constructor
    (``def __init__(self, data=None, queryset=None, *, request=None, prefix=None)``)
    and exposes it as ``self.request`` — the seam ``DjangoFilterBackend`` fills
    view-side, and the one that request-scoped filters read: ``self.request.user``
    scoping, a ``ModelChoiceFilter(queryset=lambda request: …)``, an ``__init__`` /
    ``qs`` override. We forward the request into that seam **only when the
    constructor declares it**, so those FilterSets behave the same on a spec as
    behind ``DjangoFilterBackend`` instead of hitting ``self.request is None`` (an
    ``AttributeError`` → 500); a bare ``(data, queryset)`` stand-in that never
    declares ``request`` is called exactly as before.

    Forwarding is sound on every transport, and is *not* a coupling this hook
    invents: ``request`` is always present at the shaping call site — the same
    object ``extend_queryset`` receives one branch above — real on the HTTP / MCP
    paths, and a synthetic ``build_offline_context`` request off-HTTP where
    ``.user`` and ``.query_params`` are faithful (deeper HTTP attributes — headers,
    ``META``, session — are best-effort there, exactly as they already are for
    ``extend_queryset`` and every context provider that reads the offline request).

    Detected via :func:`inspect.signature`, which resolves a class to its
    ``__init__``: a declared ``request`` (positional-or-keyword or keyword-only)
    parameter counts, as does a ``**kwargs`` catch-all.
    """
    parameters = inspect.signature(filter_set).parameters.values()
    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters):
        return True
    return any(
        param.name == "request"
        and param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        for param in parameters
    )


def materialize_retrieve(result: Any) -> Any:
    """Collapse a RETRIEVE selector's return to the single instance, or ``None``.

    The one definition of what ``kind=RETRIEVE`` means once the selector has run:
    a QuerySet materializes through ``.first()`` (so an author can write
    ``selector=lambda *, pk: Model.objects.filter(pk=pk)`` and still get the
    spec's shaping applied first), anything else passes through as the resolved
    object.

    Shared by the HTTP path and ``dispatch_spec`` deliberately. What each does
    with a ``None`` differs — HTTP raises ``NotFound`` unless ``allow_none``, the
    neutral path returns a ``not_found`` result for the transport to map — but
    *how the value is arrived at* must not, or ``kind`` would quietly mean two
    things.
    """
    return result.first() if is_queryset(result) else result


async def amaterialize_retrieve(result: Any) -> Any:
    """Async twin of :func:`materialize_retrieve` — ``.afirst()`` off the loop.

    Separate because the materialization itself is the query: ``.first()`` would
    block the event loop, so the async dispatcher must ``await .afirst()``. Same
    rule, different await — keep the two in step.
    """
    return await result.afirst() if is_queryset(result) else result


def check_view_object_permissions(
    spec: Any,
    context: Any,
    *,
    instance: Any = None,
) -> None:
    """:class:`TargetGuard` running DRF's object-permission check for an HTTP view.

    The HTTP counterpart of :func:`~rest_framework_services.enforce_permissions`:
    off HTTP a transport enforces ``spec.permission_classes`` itself, while a DRF
    view has already instantiated them and exposes
    ``check_object_permissions``. Both plug into the same ``on_target_resolved``
    seam, so the core stays authz-agnostic on every transport.

    Gated on ``Model`` for the same reason ``enforce_permissions`` is: the core
    fires this hook on the LIST branch too, with the resolved **queryset**.
    Object permissions are a per-row concept, and
    ``has_object_permission(request, view, <QuerySet>)`` would raise or silently
    mis-authorize. ``None`` (a create, or a bulk list payload) is skipped for the
    same reason.
    """
    if isinstance(instance, Model):
        context.view.check_object_permissions(context.request, instance)


def dispatch_selector_for_spec(view: Any, spec: SelectorSpec[Any, Any]) -> Any:
    """End-to-end dispatch for one ``SelectorSpec`` call from a DRF view.

    Resolves the view's ``get_selector_kwargs`` / ``get_<action>_selector_kwargs``
    chain into a :class:`ViewHooks` carrier, hands off to the one
    :func:`~rest_framework_services.dispatch_spec` core, and translates the
    neutral :class:`DispatchResult` into the view-layer contract: a RETRIEVE that
    resolved nothing raises :exc:`~rest_framework.exceptions.NotFound`, unless the
    spec sets ``allow_none=True`` (then ``None``, and the retrieve views render
    200 + JSON ``null``). ``SelectorKind.LIST`` returns the shaped queryset.

    **``argument_binding=BUNDLE`` is what keeps HTTP semantics.** Off HTTP the
    flat ``params`` mapping *is* the argument channel, so a selector spreads it
    (``SPREAD_AUTHOR_WINS``). Over HTTP it is not: a selector's kwargs come from
    route captures plus the hook chain, and the query string belongs to
    ``filter_set`` / the filter backends (see the filtering note in
    ``CLAUDE.md``). ``BUNDLE`` spreads nothing, so passing ``query_params`` here
    feeds the filter without widening the argument channel — the difference
    between the transports is expressed as the policy it already is, rather than
    as a second pipeline.

    There is deliberately no ``extra_url_kwargs`` parameter: the core reads
    ``view.kwargs`` itself via ``view_url_kwargs``, which **strips the reserved
    pool seeds**. That strip is the point — the previous view-local pool spread
    route captures *over* ``base_pool``, so a nested route like
    ``/users/<user>/posts/`` let the captured value shadow the authenticated
    ``user``. Taking the mapping from the caller would reopen it, and every call
    site passed ``view.kwargs`` verbatim anyway.

    The caller must check ``spec.selector is not None`` before calling and fall
    back to vanilla DRF otherwise.
    """
    # Local import: ``dispatch_spec`` composes ``run_selector`` /
    # ``materialize_retrieve`` from this module, so the dependency is
    # one-directional only at runtime — the same proven cycle
    # ``views.mutation.utils`` documents for the same import.
    from rest_framework_services.dispatch.dispatch_spec import dispatch_spec

    assert spec.selector is not None  # noqa: S101 — caller guarantees this
    request = view.request
    result = dispatch_spec(
        spec,
        user=getattr(request, "user", None),
        params=request.query_params,
        request=request,
        view=view,
        argument_binding=ArgumentBinding.BUNDLE,
        on_target_resolved=check_view_object_permissions,
        view_hooks=resolve_view_hooks(view, request, chain="selector"),
    )
    if result.kind == "not_found":
        raise NotFound()
    return result.value
