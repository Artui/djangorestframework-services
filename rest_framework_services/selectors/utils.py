"""Internal selector dispatch helpers (sync + async)."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.db.models import QuerySet
from django.db.models.manager import BaseManager
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request

from rest_framework_services.dispatch.base_pool import base_pool
from rest_framework_services.is_async import is_async
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.views.utils import (
    resolve_callable_kwargs,
    resolve_extra_kwargs,
)


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


def dispatch_selector_for_spec(
    view: Any,
    spec: SelectorSpec[Any, Any],
    *,
    extra_url_kwargs: dict[str, Any] | None = None,
    source_label: str = "SelectorSpec.selector",
) -> Any:
    """End-to-end dispatch for one ``SelectorSpec`` call.

    Runs the kwargs-resolution chain (``spec.kwargs`` →
    ``get_<action>_selector_kwargs`` → ``get_selector_kwargs``), filters
    the resulting pool against the selector's signature, invokes the
    selector sync-or-async, then applies declarative + dynamic queryset
    shaping. Used by both selector viewset mixins and the standalone
    selector views so the call shape lives in one place.

    When ``spec.kind`` is :attr:`SelectorKind.RETRIEVE`, the returned
    QuerySet (if any) is materialized via ``.first()`` and ``None`` /
    :exc:`~django.core.exceptions.ObjectDoesNotExist` are translated to
    :exc:`~rest_framework.exceptions.NotFound` — unless the spec sets
    ``allow_none=True``, in which case the missing-object case returns
    ``None`` and the calling view decides how to render it (the retrieve
    views render 200 + JSON ``null``). ``SelectorKind.LIST`` returns the
    (optionally shaped) selector return as-is.

    The caller must check ``spec.selector is not None`` before calling and
    fall back to vanilla DRF otherwise.

    ``source_label`` is forwarded to :func:`apply_queryset_shaping` for
    error messages, so a misconfiguration on a nested
    ``output_selector_spec`` points at the right place.
    """
    selector = spec.selector
    assert selector is not None  # noqa: S101 — caller guarantees this
    request = view.request
    action: str | None = getattr(view, "action", None)
    action_hook: str | None = f"get_{action}_selector_kwargs" if action else None

    try:
        extras = resolve_extra_kwargs(
            view,
            request,
            spec_kwargs=spec.kwargs,
            action_hook=action_hook,
            catch_all_hook="get_selector_kwargs",
        )
        # See the mutation path: the seeds come from ``base_pool`` so HTTP and
        # off-HTTP dispatch cannot drift on what a callable may declare.
        pool: dict[str, Any] = {
            **base_pool(user=getattr(request, "user", None), request=request),
            **(extra_url_kwargs or {}),
            **extras,
        }
        result = run_selector(selector, resolve_callable_kwargs(selector, pool))
        result = apply_queryset_shaping(
            result,
            view,
            request,
            select_related=spec.select_related,
            prefetch_related=spec.prefetch_related,
            annotations=spec.annotations,
            extend_queryset=spec.extend_queryset,
            filter_set=spec.filter_set,
            source_label=source_label,
        )
    except ObjectDoesNotExist as exc:
        if spec.kind is SelectorKind.RETRIEVE:
            if spec.allow_none:
                return None
            raise NotFound() from exc
        raise

    if spec.kind is not SelectorKind.RETRIEVE:
        return result
    instance = result.first() if is_queryset(result) else result
    if instance is None and not spec.allow_none:
        raise NotFound()
    return instance
