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

    The one definition of a queryset-shaping target: what the shaping fields may
    be applied to, and what a RETRIEVE selector materializes from. Tested by type
    rather than by ``hasattr(…, "first")``, which would also match a domain
    object that happens to expose ``first``. ``QuerySet`` subclasses all pass.
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

    The order is fixed: the declarative fields first (in declaration order),
    then ``extend_queryset``, so the user callable always sees the fully
    statically-shaped queryset, and finally ``filter_set``, so filtering
    composes with shaping and runs before the retrieve ``.first()``
    materialization the caller does next.

    Args:
        request: Passed to ``extend_queryset``, and into the ``filter_set``
            constructor when it declares a ``request`` parameter — so a
            request-scoped ``FilterSet`` sees the same ``self.request`` it would
            behind ``DjangoFilterBackend``. A bare ``(data, queryset)`` stand-in
            is called exactly as before.
        filter_set: Applied by duck typing as
            ``filter_set(data=filter_data, queryset=qs).qs``, after validation
            (mirroring ``DjangoFilterBackend``'s 400 on invalid filter input).
        filter_data: The flat ``{field: value}`` mapping the FilterSet reads.
            ``None`` falls back to ``request.query_params`` — the HTTP view
            path; a transport-neutral caller passes its own params.
        source_label: Named in the misconfiguration error to point at the
            offending callable (``"SelectorSpec.selector"`` vs
            ``"ServiceSpec.output_selector_spec.selector"``).

    Returns:
        The shaped queryset, or ``qs`` unchanged when nothing is configured.

    Raises:
        ImproperlyConfigured: Shaping is configured but ``qs`` is not a Django
            queryset — loud failure beats a stray ``AttributeError`` deep in DRF
            rendering.
        ValidationError: ``filter_set`` rejected ``filter_data``.
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

    Reading ``.qs`` without ``is_valid()`` first silently returns the
    **unfiltered** queryset in django-filter's default non-strict mode, so a bad
    ``?field=`` value would answer 200 with unfiltered rows; ``filter_set``
    replaces that backend on the list path and must keep its 400. Only enforced
    when the duck-typed ``filter_set`` exposes ``is_valid``, so a bare
    ``(data, queryset) -> .qs`` stand-in keeps its pass-through behaviour, and
    built straight from ``filterset.errors`` so the core never imports
    django-filter.
    """
    is_valid = getattr(filterset, "is_valid", None)
    if is_valid is not None and not is_valid():
        raise ValidationError(filterset.errors)


def _filter_set_accepts_request(filter_set: Any) -> bool:
    """True when ``filter_set``'s constructor declares a ``request`` parameter.

    ``filter_set`` is duck-typed on ``(data, queryset) -> .qs`` alone, so the
    package imports no django-filter. A real ``FilterSet`` *also* takes
    ``request`` and exposes it as ``self.request`` — the seam
    ``DjangoFilterBackend`` fills view-side and request-scoped filters read.
    Forwarding it only when the constructor declares it keeps those working
    while a bare stand-in is called exactly as before.

    :func:`inspect.signature` resolves a class to its ``__init__``: a declared
    ``request`` (positional-or-keyword or keyword-only) counts, as does a
    ``**kwargs`` catch-all.
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
    a queryset materializes through ``.first()`` — so an author can write
    ``selector=lambda *, pk: Model.objects.filter(pk=pk)`` and still get the
    spec's shaping applied first — and anything else passes through as the
    resolved object. What each caller does with a ``None`` differs; how the value
    is arrived at does not.
    """
    return result.first() if is_queryset(result) else result


async def amaterialize_retrieve(result: Any) -> Any:
    """Async twin of :func:`materialize_retrieve`, awaiting ``.afirst()``.

    Separate because the materialization *is* the query — ``.first()`` would
    block the event loop. Same rule otherwise; keep the two in step.
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
    view has already instantiated them and exposes ``check_object_permissions``.
    Both plug into the same ``on_target_resolved`` seam, so the core stays
    authz-agnostic on every transport.

    Gated on ``Model``, like ``enforce_permissions``: the core fires this hook on
    the LIST branch too, with the resolved **queryset**, and object permissions
    are per-row. ``None`` — a create, or a bulk list payload — is skipped too.
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
    flat ``params`` mapping *is* the argument channel; over HTTP a selector's
    kwargs come from route captures plus the hook chain, and the query string
    belongs to ``filter_set`` / the filter backends. ``BUNDLE`` spreads nothing,
    so passing ``query_params`` feeds the filter without widening the argument
    channel.

    There is deliberately no ``extra_url_kwargs`` parameter: the core reads
    ``view.kwargs`` itself via ``view_url_kwargs``, which **strips the reserved
    pool seeds**, so a nested route like ``/users/<user>/posts/`` cannot let the
    captured value shadow the authenticated ``user``. Taking the mapping from a
    caller would reopen that.

    The caller must check ``spec.selector is not None`` first and fall back to
    vanilla DRF otherwise.
    """
    # Local import: ``dispatch_spec`` composes ``run_selector`` /
    # ``materialize_retrieve`` from this module, so the cycle is real and the
    # dependency one-directional only at runtime.
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
