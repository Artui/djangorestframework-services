"""Fail-fast validation of service / selector signatures at view setup time.

The framework already filters its kwargs pool through
:func:`resolve_callable_kwargs` at request time, which means a service that
declares a required kw-only parameter the framework cannot provide fails
late, deep in the dispatch path with a generic
``TypeError: missing required keyword-only argument`` message. The helpers
here surface those errors at ``as_view()`` time with a precise diagnostic so
misconfigurations turn up at module import / URL wiring instead of at the
first request.

The validator is intentionally lenient on extras: when a callable could be
fed by ``ServiceSpec.kwargs`` / ``SelectorSpec.kwargs`` or by an overridden
``get_*_kwargs`` method, the validator assumes those overrides supply
whatever the signature needs and only fails on the unambiguous misuses.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import BasePermission

from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

# Keys the framework injects automatically into the pool.
_FRAMEWORK_KEY_DATA = "data"
_FRAMEWORK_KEY_INSTANCE = "instance"
_FRAMEWORK_KEY_RESULT = "result"
_FRAMEWORK_KEY_SERIALIZER = "serializer"


def _required_kw_params(fn: Callable[..., Any]) -> dict[str, inspect.Parameter]:
    """Return the kw-resolvable params of ``fn`` with no default, keyed by name.

    Includes ``POSITIONAL_OR_KEYWORD`` and ``KEYWORD_ONLY`` parameters that
    have no default value. Skips ``VAR_POSITIONAL`` / ``VAR_KEYWORD`` and
    parameters with defaults (those are optional from the framework's POV).
    """
    sig = inspect.signature(fn)
    return {
        name: p
        for name, p in sig.parameters.items()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        and p.default is inspect.Parameter.empty
    }


def _accepts_var_keyword(fn: Callable[..., Any]) -> bool:
    return any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in inspect.signature(fn).parameters.values()
    )


def validate_callable_signature(
    fn: Callable[..., Any],
    *,
    spec_label: str,
    has_data: bool,
    has_instance: bool,
    has_result: bool,
    spec_kwargs: Callable[..., Any] | None,
    permissive_extras: bool,
    extra_known_keys: Iterable[str] = (),
) -> None:
    """Raise :exc:`ImproperlyConfigured` on a misconfigured service / selector.

    ``has_data`` / ``has_instance`` / ``has_result`` describe whether those
    framework-injected keys will be present for *this* call site. Mismatches
    on those (e.g. a service requiring ``data`` when ``input_serializer`` is
    unset) always fail — they cannot be papered over by the user.

    For other required kw-only parameters the validator is lenient: if
    ``permissive_extras`` is ``True`` (because the view overrides
    ``get_*_kwargs`` or ``spec_kwargs`` is supplied) it assumes those
    overrides contribute the missing keys and skips the check. Otherwise it
    raises with a clear hint.

    ``extra_known_keys`` lets callers extend the always-allowed set
    (e.g. selectors include the URL kwargs they expect to see).
    """
    if _accepts_var_keyword(fn):
        return

    fn_label = getattr(fn, "__qualname__", repr(fn))
    required = _required_kw_params(fn)

    # Always-fatal mismatches: framework-provided keys that don't exist in this
    # call site.
    if _FRAMEWORK_KEY_DATA in required and not has_data:
        raise ImproperlyConfigured(
            f"{spec_label}: {fn_label} requires `data` but the spec has no "
            "`input_serializer`. Either set `input_serializer=...` on the spec "
            "or remove `data` from the signature."
        )
    if _FRAMEWORK_KEY_SERIALIZER in required and not has_data:
        raise ImproperlyConfigured(
            f"{spec_label}: {fn_label} requires `serializer` but the spec has "
            "no `input_serializer` to bind one from. Either set "
            "`input_serializer=...` on the spec or remove `serializer` from "
            "the signature."
        )
    if _FRAMEWORK_KEY_INSTANCE in required and not has_instance:
        raise ImproperlyConfigured(
            f"{spec_label}: {fn_label} requires `instance` but this action "
            "does not provide one (e.g. a create or list action). Remove `instance` "
            "from the signature or attach the spec to an update / destroy / "
            "retrieve / detail action instead."
        )
    if _FRAMEWORK_KEY_RESULT in required and not has_result:
        raise ImproperlyConfigured(
            f"{spec_label}: {fn_label} requires `result` but this callable "
            "is not an output selector. Remove `result` from the signature or "
            "attach the callable to `ServiceSpec.output_selector_spec.selector`."
        )

    if permissive_extras or spec_kwargs is not None:
        # User is plugging in their own kwargs source; the framework cannot
        # statically know what they provide, so don't second-guess them.
        return

    known: set[str] = {"request", "user"}
    if has_data:
        known.add(_FRAMEWORK_KEY_DATA)
        known.add(_FRAMEWORK_KEY_SERIALIZER)
    if has_instance:
        known.add(_FRAMEWORK_KEY_INSTANCE)
    if has_result:
        known.add(_FRAMEWORK_KEY_RESULT)
    known.update(extra_known_keys)

    unknown_required = sorted(name for name in required if name not in known)
    if not unknown_required:
        return

    raise ImproperlyConfigured(
        f"{spec_label}: {fn_label} has required parameter(s) "
        f"{unknown_required!r} that the framework does not provide. "
        "Provide them via `ServiceSpec.kwargs` / `SelectorSpec.kwargs`, via "
        "`get_<action>_service_kwargs` / `get_service_kwargs`, or remove "
        "them from the signature."
    )


def is_overridden(view_cls: type, base_cls: type, method_name: str) -> bool:
    """Return ``True`` if ``view_cls`` overrides ``base_cls``'s ``method_name``.

    Used to decide whether the framework should assume an ``get_*_kwargs``
    override is contributing extras (and therefore relax signature
    validation for that callable).
    """
    base = getattr(base_cls, method_name, None)
    if base is None:
        return hasattr(view_cls, method_name)
    return getattr(view_cls, method_name, None) is not base


def _has_any_shaping(spec: SelectorSpec[Any, Any]) -> bool:
    return (
        spec.select_related is not None
        or spec.prefetch_related is not None
        or spec.annotations is not None
        or spec.extend_queryset is not None
        or spec.filter_set is not None
    )


def _validate_selector_shaping(
    spec: SelectorSpec[Any, Any],
    *,
    label: str,
) -> None:
    """Raise :exc:`ImproperlyConfigured` when shaping is set without a selector.

    ``select_related`` / ``prefetch_related`` / ``annotations`` /
    ``extend_queryset`` / ``filter_set`` only run inside
    :func:`dispatch_selector_for_spec`, which is skipped when
    ``spec.selector is None``. Catching the misuse at ``as_view()`` time
    beats a silent no-op at request time.
    """
    if spec.selector is None and _has_any_shaping(spec):
        raise ImproperlyConfigured(
            f"{label}: select_related / prefetch_related / annotations / "
            "extend_queryset / filter_set are set but `selector` is not. Set a "
            "selector or drop the shaping fields — they only run when the "
            "spec's selector dispatches."
        )


def _uses_django_filter_backend(view_cls: type) -> bool:
    """True when ``view_cls.filter_backends`` includes a ``DjangoFilterBackend``.

    Detected by class name across each backend's MRO (so subclasses count)
    rather than ``isinstance``: ``django-filter`` is an optional dependency
    this package never imports, so the check must hold whether or not it is
    installed.
    """
    backends = getattr(view_cls, "filter_backends", None) or ()
    return any(
        any(
            getattr(klass, "__name__", "") == "DjangoFilterBackend"
            for klass in getattr(backend, "__mro__", ())
        )
        for backend in backends
    )


def validate_filter_set_no_backend_conflict(
    view_cls: type,
    spec: SelectorSpec[Any, Any],
    *,
    label: str,
) -> None:
    """Reject a list selector that sets ``filter_set`` *and* wires ``DjangoFilterBackend``.

    On the list path DRF's ``list()`` runs ``filter_queryset()`` over
    ``filter_backends`` while the dispatcher *also* applies
    ``spec.filter_set`` — so a queryset is filtered twice. The two are
    equivalent (``DjangoFilterBackend`` does
    ``filterset_class(query_params, qs, request).qs``), so ``filter_set``
    **replaces** the backend; configuring both for one action is the
    misconfiguration this catches at ``as_view()`` time.

    Callers gate this on the **list** path only. Retrieve has no such
    conflict: the selector retrieve path overrides ``get_object()`` and never
    calls ``filter_queryset``, so ``filter_set`` is the only filter applied.
    """
    if spec.filter_set is None or not _uses_django_filter_backend(view_cls):
        return
    raise ImproperlyConfigured(
        f"{label}: spec.filter_set is set and the view's filter_backends "
        "includes DjangoFilterBackend. `filter_set` replaces "
        "DjangoFilterBackend (both apply a FilterSet to the list queryset), so "
        "the queryset would be filtered twice. Drop DjangoFilterBackend from "
        "filter_backends for this action, or remove filter_set from the spec."
    )


def _validate_permission_classes(
    permission_classes: Any,
    *,
    label: str,
) -> None:
    """Raise :exc:`ImproperlyConfigured` on a malformed ``permission_classes``.

    ``None`` is the inherit-from-view default and skips validation. Every
    entry must be a subclass of DRF's :class:`BasePermission`; instances
    (a common typo of ``[MyPermission()]``) and unrelated classes fail fast
    at ``as_view()`` time.
    """
    if permission_classes is None:
        return
    for entry in permission_classes:
        if not isinstance(entry, type) or not issubclass(entry, BasePermission):
            raise ImproperlyConfigured(
                f"{label}: permission_classes entries must be `BasePermission` "
                f"subclasses; got {entry!r}."
            )


def _validate_output_selector_spec(
    output_spec: SelectorSpec[Any, Any],
    *,
    label: str,
    has_instance: bool,
    has_collection: bool,
    permissive_extras: bool,
    spec_kwargs: Callable[..., Any] | None,
    input_serializer: type | None,
) -> None:
    """Validate the nested :class:`SelectorSpec` on :attr:`ServiceSpec.output_selector_spec`.

    ``output_spec.kind`` declares the response cardinality:
    :attr:`SelectorKind.RETRIEVE` (the default) re-fetches a single instance;
    :attr:`SelectorKind.LIST` re-fetches and renders a *set* and is valid only
    alongside ``collection_selector_spec`` — bulk output pairs with a bulk
    operation, and a single-instance mutation returns one representation. The
    nested spec's ``selector`` is validated with ``has_result=True`` (the
    service's return joins the selector's kwargs pool as ``result``) and the
    surrounding mutation's ``kwargs`` / view-level ``get_*_service_kwargs``
    chain is what feeds the extras — the nested spec's own ``kwargs`` /
    ``permission_classes`` are ignored at request time, so we don't validate
    them as a selector spec would.
    """
    if output_spec.kind is SelectorKind.LIST and not has_collection:
        raise ImproperlyConfigured(
            f"{label}: output_selector_spec.kind=LIST renders a list and is only "
            "valid alongside collection_selector_spec (bulk output pairs with a "
            "bulk operation). A single-instance mutation returns one "
            "representation — use kind=SelectorKind.RETRIEVE."
        )
    _validate_selector_shaping(output_spec, label=label)
    if output_spec.selector is not None:
        validate_callable_signature(
            output_spec.selector,
            spec_label=f"{label}.selector",
            has_data=input_serializer is not None,
            has_instance=has_instance,
            has_result=True,
            spec_kwargs=spec_kwargs,
            permissive_extras=permissive_extras,
        )


def _validate_instance_selector_spec(
    instance_spec: SelectorSpec[Any, Any],
    *,
    label: str,
    has_instance: bool,
) -> None:
    """Validate the nested :class:`SelectorSpec` on :attr:`ServiceSpec.instance_selector_spec`.

    Instance resolution is always retrieve-shaped, so ``kind`` must be
    :attr:`SelectorKind.RETRIEVE`. The spec is only consulted on actions
    that target an instance — configuring it on a create / non-detail
    action would silently never run, so that fails fast too. The selector
    runs *before* input validation against the ``{request, user}`` + URL
    kwargs pool, so requesting ``data`` / ``instance`` / ``result`` is
    always a misuse; other extras stay permissive (URL kwargs and the
    selector kwargs chain are dynamic).
    """
    if not has_instance:
        raise ImproperlyConfigured(
            f"{label}: instance_selector_spec is set but this action does not "
            "target an instance (e.g. a create or non-detail action). Remove "
            "it or attach the spec to an update / destroy / detail action."
        )
    if instance_spec.kind is not SelectorKind.RETRIEVE:
        raise ImproperlyConfigured(
            f"{label}: instance_selector_spec.kind must be SelectorKind.RETRIEVE; "
            f"got {instance_spec.kind!r}. Instance resolution materializes a "
            "single instance."
        )
    _validate_selector_shaping(instance_spec, label=label)
    if instance_spec.selector is not None:
        validate_callable_signature(
            instance_spec.selector,
            spec_label=f"{label}.selector",
            has_data=False,
            has_instance=False,
            has_result=False,
            spec_kwargs=instance_spec.kwargs,
            permissive_extras=True,
        )


def _validate_collection_selector_spec(
    collection_spec: SelectorSpec[Any, Any],
    *,
    label: str,
) -> None:
    """Validate the nested :class:`SelectorSpec` on ``ServiceSpec.collection_selector_spec``.

    A collection target resolves a *set*, so ``kind`` must be
    :attr:`SelectorKind.LIST` (the LIST twin of ``instance_selector_spec``'s
    RETRIEVE) and a ``selector`` is required — there is no view fallback. The
    selector runs against ``{request, user}`` + the dispatch params, so its
    extras stay permissive.
    """
    if collection_spec.kind is not SelectorKind.LIST:
        raise ImproperlyConfigured(
            f"{label}: collection_selector_spec.kind must be SelectorKind.LIST; "
            f"got {collection_spec.kind!r}. It resolves a collection, not a single instance."
        )
    if collection_spec.selector is None:
        raise ImproperlyConfigured(
            f"{label}: collection_selector_spec requires a `selector` resolving the target set."
        )
    _validate_selector_shaping(collection_spec, label=label)
    validate_callable_signature(
        collection_spec.selector,
        spec_label=f"{label}.selector",
        has_data=False,
        has_instance=False,
        has_result=False,
        spec_kwargs=collection_spec.kwargs,
        permissive_extras=True,
    )


# Keys the status pool offers a ``success_status`` callable.
_SUCCESS_STATUS_KEYS = frozenset({"result", "instance", "request", "view"})


def _validate_success_status(
    success_status: int | Callable[..., int] | None, *, label: str
) -> None:
    """Reject a ``success_status`` that is neither int/None nor a well-formed callable.

    A callable may declare any subset of the status pool
    (``result`` / ``instance`` / ``request`` / ``view``) or ``**kwargs``; a
    required parameter outside that set can never be supplied, so it fails fast
    here rather than as a ``TypeError`` deep in dispatch.
    """
    if success_status is None or isinstance(success_status, int):
        return
    if not callable(success_status):
        raise ImproperlyConfigured(
            f"{label}: `success_status` must be an int, a callable returning an int, "
            f"or None — got {type(success_status).__name__}."
        )
    if _accepts_var_keyword(success_status):
        return
    unknown = set(_required_kw_params(success_status)) - _SUCCESS_STATUS_KEYS
    if unknown:
        raise ImproperlyConfigured(
            f"{label}: `success_status` callable requires parameter(s) "
            f"{sorted(unknown)} the framework can't supply — declare only a subset "
            f"of {sorted(_SUCCESS_STATUS_KEYS)} (or `**kwargs`)."
        )


def validate_service_spec(
    spec: ServiceSpec[Any, Any, Any],
    *,
    label: str,
    has_instance: bool,
    permissive_extras: bool,
) -> None:
    """Validate a :class:`ServiceSpec`'s ``service`` and nested selector specs.

    Shared between standalone mutation views, viewset mixins, and
    ``@service_action``. ``has_instance`` is fixed by the action context
    (``False`` for create, ``True`` for update / destroy / detail actions).
    """
    _validate_success_status(spec.success_status, label=label)
    if spec.many and spec.collection_selector_spec is not None:
        raise ImproperlyConfigured(
            f"{label}: `many` and `collection_selector_spec` are mutually exclusive "
            "— a list-payload bulk and a collection-target bulk are different shapes."
        )
    _validate_permission_classes(spec.permission_classes, label=label)
    validate_callable_signature(
        spec.service,
        spec_label=label,
        has_data=spec.input_serializer is not None,
        has_instance=has_instance,
        has_result=False,
        spec_kwargs=spec.kwargs,
        permissive_extras=permissive_extras,
        # A collection-target service receives the resolved set as ``collection``.
        extra_known_keys=("collection",) if spec.collection_selector_spec is not None else (),
    )
    if spec.collection_selector_spec is not None:
        _validate_collection_selector_spec(
            spec.collection_selector_spec,
            label=f"{label}.collection_selector_spec",
        )
    if spec.instance_selector_spec is not None:
        _validate_instance_selector_spec(
            spec.instance_selector_spec,
            label=f"{label}.instance_selector_spec",
            has_instance=has_instance,
        )
    if spec.output_selector_spec is not None:
        _validate_output_selector_spec(
            spec.output_selector_spec,
            label=f"{label}.output_selector_spec",
            has_instance=has_instance,
            has_collection=spec.collection_selector_spec is not None,
            permissive_extras=permissive_extras,
            spec_kwargs=spec.kwargs,
            input_serializer=spec.input_serializer,
        )


def validate_selector_spec(
    spec: SelectorSpec[Any, Any],
    *,
    label: str,
    expected_kind: SelectorKind | None = None,
) -> None:
    """Validate a :class:`SelectorSpec`'s ``selector``.

    Selectors are always permissive on extras (URL kwargs and
    ``get_selector_kwargs`` are dynamic), so the only fatal misuses are
    requesting framework-only keys that don't exist in the selector pool
    (``data``, ``instance``, ``result``).

    ``expected_kind`` (when supplied) fails fast if ``spec.kind`` does not
    match — e.g. a ``LIST`` spec mounted on :class:`SelectorRetrieveView`
    raises at ``as_view()`` time rather than producing surprising
    runtime behaviour.
    """
    _validate_permission_classes(spec.permission_classes, label=label)
    _validate_selector_shaping(spec, label=label)
    if expected_kind is not None and spec.kind is not expected_kind:
        raise ImproperlyConfigured(
            f"{label}: spec.kind is {spec.kind!r} but this mount point "
            f"expects {expected_kind!r}. Construct the spec with "
            f"kind={expected_kind!r} or move it to the matching view."
        )
    if spec.selector is None:
        return
    validate_callable_signature(
        spec.selector,
        spec_label=label,
        has_data=False,
        has_instance=False,
        has_result=False,
        spec_kwargs=spec.kwargs,
        permissive_extras=True,
    )


def validate_mutation_view_spec(
    view_cls: type,
    *,
    has_instance: bool,
) -> None:
    """Validate ``view_cls.spec`` on a standalone mutation view.

    No-op when ``spec`` is unset (the base classes inherit a ``None``
    placeholder so ``as_view()`` itself doesn't trip).
    """
    spec: ServiceSpec[Any, Any, Any] | None = getattr(view_cls, "spec", None)
    if spec is None:
        return
    # Local import to avoid a module-level cycle:
    # ``views.spec_validation`` is imported by ``views.mutation.*``, but the
    # reverse coupling only matters here (to detect catch-all overrides).
    from rest_framework_services.views.mutation.mutation_flow_mixin import (
        MutationFlowMixin,
    )

    validate_service_spec(
        spec,
        label=f"{view_cls.__name__}.spec",
        has_instance=has_instance,
        permissive_extras=is_overridden(view_cls, MutationFlowMixin, "get_service_kwargs"),
    )


def validate_selector_view_spec(
    view_cls: type,
    *,
    expected_kind: SelectorKind,
) -> None:
    """Validate ``view_cls.spec`` on a standalone selector view.

    No-op when ``spec`` is unset (the spec then means "use vanilla DRF" and
    there is nothing to validate). ``expected_kind`` is the kind the view
    is shaped for (``LIST`` for :class:`SelectorListView`, ``RETRIEVE``
    for :class:`SelectorRetrieveView`); a spec whose ``kind`` does not
    match fails fast at ``as_view()`` time.
    """
    spec: SelectorSpec[Any, Any] | None = getattr(view_cls, "spec", None)
    if spec is None:
        return
    label = f"{view_cls.__name__}.spec"
    validate_selector_spec(spec, label=label, expected_kind=expected_kind)
    if expected_kind is SelectorKind.LIST:
        validate_filter_set_no_backend_conflict(view_cls, spec, label=label)
