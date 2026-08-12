"""Fail-fast validation of service / selector signatures at view setup time.

:func:`resolve_callable_kwargs` forwards pool∩signature, so a required parameter
the framework cannot supply is *omitted* rather than rejected and the call dies
as a bare ``TypeError`` deep in dispatch, at the first request. These helpers
surface the same misconfiguration at ``as_view()`` time with a precise message.

Deliberately lenient on extras: a ``kwargs`` provider or an overridden
``get_*_kwargs`` may be feeding the callable keys the validator cannot see.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import BasePermission

from rest_framework_services.types.polymorphic_service_spec import PolymorphicServiceSpec
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

    A parameter with a default is optional from the framework's point of view.
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

    ``has_data`` / ``has_instance`` / ``has_result`` say whether those
    framework-injected keys exist at *this* call site; requiring one that does
    not always fails, since no user override can supply it. Every other required
    parameter is checked only when nothing could be feeding it
    (``permissive_extras`` false and no ``spec_kwargs``). ``extra_known_keys``
    extends the allowed set for call sites that seed additional names.
    """
    if _accepts_var_keyword(fn):
        return

    fn_label = getattr(fn, "__qualname__", repr(fn))
    required = _required_kw_params(fn)

    # Always-fatal: framework-provided keys absent from this call site.
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
        # A user-supplied kwargs source is in play and its keys are not
        # statically knowable, so anything below would be a guess.
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

    Drives ``permissive_extras``: an overridden ``get_*_kwargs`` is assumed to
    contribute keys the validator cannot see.
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

    Shaping only runs inside :func:`dispatch_selector_for_spec`, which is skipped
    when ``spec.selector is None`` — so without this the fields are a silent
    no-op at request time.
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

    Matched by name across each backend's MRO (so subclasses count) rather than
    ``isinstance``: ``django-filter`` is an optional dependency this package
    never imports, so the check has to hold whether or not it is installed.
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

    On the list path DRF's ``list()`` runs ``filter_queryset()`` while the
    dispatcher also applies ``spec.filter_set`` — equivalent operations, so the
    queryset would be filtered twice. Callers must gate this on the **list** path
    only: the selector retrieve path overrides ``get_object()`` and never calls
    ``filter_queryset``, so there is no conflict there.
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

    ``None`` is the inherit-from-view default and skips validation. Catches the
    common ``[MyPermission()]`` typo, which DRF would otherwise call as if it
    were a class.
    """
    if permission_classes is None:
        return
    for entry in permission_classes:
        if not isinstance(entry, type) or not issubclass(entry, BasePermission):
            raise ImproperlyConfigured(
                f"{label}: permission_classes entries must be `BasePermission` "
                f"subclasses; got {entry!r}."
            )


def _validate_preconditions(
    preconditions: Any,
    *,
    label: str,
    has_data: bool,
    has_instance: bool,
    spec_kwargs: Callable[..., Any] | None,
    permissive_extras: bool,
    extra_known_keys: tuple[str, ...] = (),
) -> None:
    """Fail fast on a mis-declared ``preconditions``.

    Both failures it catches are otherwise a 500 at request time: a bare callable
    or string passed instead of a sequence would be iterated element-wise inside
    dispatch, and a predicate naming a parameter no seed provides gets a
    missing-argument ``TypeError`` deep in the stack. Uses the same signature
    check the service gets, so the pool a precondition may declare from is
    exactly the pool it will be handed.
    """
    if preconditions is None:
        return
    if callable(preconditions) or isinstance(preconditions, str | bytes):
        raise ImproperlyConfigured(
            f"{label}: `preconditions` takes a sequence of callables, not a single "
            f"{type(preconditions).__name__}. Wrap it in a list: preconditions=[…]."
        )
    if not isinstance(preconditions, Iterable):
        raise ImproperlyConfigured(
            f"{label}: `preconditions` must be a sequence of callables, got "
            f"{type(preconditions).__name__}."
        )
    for index, precondition in enumerate(preconditions):
        if not callable(precondition):
            raise ImproperlyConfigured(
                f"{label}: preconditions[{index}] is not callable ({type(precondition).__name__})."
            )
        validate_callable_signature(
            precondition,
            spec_label=f"{label}.preconditions[{index}]",
            has_data=has_data,
            has_instance=has_instance,
            has_result=False,
            spec_kwargs=spec_kwargs,
            permissive_extras=permissive_extras,
            extra_known_keys=extra_known_keys,
        )


def _reject_nested_preconditions(nested: Any, *, label: str) -> None:
    """A nested spec's ``preconditions`` never runs — say so rather than ignore it.

    Only the spec that owns the dispatch invokes preconditions.
    """
    if nested.preconditions is not None:
        raise ImproperlyConfigured(
            f"{label}: `preconditions` on a nested spec is never invoked — the "
            "surrounding spec owns the dispatch. Move them to the spec being "
            "dispatched."
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

    ``kind=LIST`` is refused without ``collection_selector_spec``: a
    single-instance mutation returns one representation. ``has_result=True``
    because the service's return joins the selector's pool as ``result``. Extras
    come from the *surrounding* mutation's kwargs chain — the nested spec's own
    ``kwargs`` / ``permission_classes`` are ignored at request time, so they are
    deliberately not validated as a selector spec's would be.
    """
    _reject_nested_preconditions(output_spec, label=label)
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

    On an action with no instance the spec would silently never run, so that
    fails fast too. The selector runs *before* input validation, against
    ``{request, user}`` plus URL kwargs — hence ``has_data`` / ``has_instance`` /
    ``has_result`` all ``False`` below, while other extras stay permissive
    because URL kwargs and the selector kwargs chain are dynamic.
    """
    _reject_nested_preconditions(instance_spec, label=label)
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

    Unlike ``instance_selector_spec``, a ``selector`` is mandatory — there is no
    view fallback for a collection target. Runs against ``{request, user}`` plus
    the dispatch params, so extras stay permissive.
    """
    _reject_nested_preconditions(collection_spec, label=label)
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

# Keys the framework offers a ``response_finalizer`` callable.
_RESPONSE_FINALIZER_KEYS = frozenset({"response", "result", "request", "view", "instance", "data"})


def _validate_success_status(
    success_status: int | Callable[..., int] | None, *, label: str
) -> None:
    """Reject a ``success_status`` that is neither int/None nor a well-formed callable.

    A required parameter outside :data:`_SUCCESS_STATUS_KEYS` can never be
    supplied, so it fails here rather than as a ``TypeError`` deep in dispatch.
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


def _validate_response_finalizer(finalizer: Any, *, label: str) -> None:
    """Reject a ``response_finalizer`` that isn't a well-formed callable.

    Same rule as :func:`_validate_success_status`, against
    :data:`_RESPONSE_FINALIZER_KEYS`.
    """
    if finalizer is None:
        return
    if not callable(finalizer):
        raise ImproperlyConfigured(
            f"{label}: `response_finalizer` must be a callable returning a Response "
            f"or None — got {type(finalizer).__name__}."
        )
    if _accepts_var_keyword(finalizer):
        return
    unknown = set(_required_kw_params(finalizer)) - _RESPONSE_FINALIZER_KEYS
    if unknown:
        raise ImproperlyConfigured(
            f"{label}: `response_finalizer` requires parameter(s) {sorted(unknown)} the "
            f"framework can't supply — declare only a subset of "
            f"{sorted(_RESPONSE_FINALIZER_KEYS)} (or `**kwargs`)."
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
    _validate_response_finalizer(spec.response_finalizer, label=label)
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
    _validate_preconditions(
        spec.preconditions,
        label=label,
        has_data=spec.input_serializer is not None,
        has_instance=has_instance,
        spec_kwargs=spec.kwargs,
        permissive_extras=permissive_extras,
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


def validate_polymorphic_service_spec(
    poly: PolymorphicServiceSpec,
    *,
    label: str,
    has_instance: bool,
    permissive_extras: bool,
) -> None:
    """Validate every variant of a :class:`PolymorphicServiceSpec` + its strategy.

    Each variant is validated with the same ``has_instance`` /
    ``permissive_extras`` as a plain entry would be.
    """
    if not poly.specs:
        raise ImproperlyConfigured(f"{label}: PolymorphicServiceSpec.specs must not be empty.")
    for key, variant in poly.specs.items():
        if not isinstance(variant, ServiceSpec):
            raise ImproperlyConfigured(
                f"{label}: variant {key!r} must be a ServiceSpec, got {type(variant).__name__}."
            )
        validate_service_spec(
            variant,
            label=f"{label}[{key!r}]",
            has_instance=has_instance,
            permissive_extras=permissive_extras,
        )
    if poly.permission_strategy == "require_identical":
        distinct = {
            None if v.permission_classes is None else tuple(v.permission_classes)
            for v in poly.specs.values()
        }
        if len(distinct) > 1:
            raise ImproperlyConfigured(
                f"{label}: permission_strategy='require_identical' but the variants declare "
                "different `permission_classes`. Make them identical or use 'union'."
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
    framework-only keys absent from the selector pool. ``expected_kind`` catches
    a spec mounted on the wrong view — a ``LIST`` spec on
    :class:`SelectorRetrieveView` would otherwise misbehave only at runtime.
    """
    _validate_permission_classes(spec.permission_classes, label=label)
    _validate_selector_shaping(spec, label=label)
    if expected_kind is not None and spec.kind is not expected_kind:
        raise ImproperlyConfigured(
            f"{label}: spec.kind is {spec.kind!r} but this mount point "
            f"expects {expected_kind!r}. Construct the spec with "
            f"kind={expected_kind!r} or move it to the matching view."
        )
    # Must stay above the ``selector is None`` bail-out: a spec can carry
    # preconditions without its own selector (the view's ``get_queryset``
    # resolves the target), and those still run.
    _validate_preconditions(
        spec.preconditions,
        label=label,
        has_data=False,
        # The target seeds one name or the other, never both — which is what
        # stops an ``instance`` precondition being written against a LIST spec.
        has_instance=spec.kind is SelectorKind.RETRIEVE,
        spec_kwargs=spec.kwargs,
        permissive_extras=True,
        extra_known_keys=("collection",) if spec.kind is SelectorKind.LIST else (),
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

    No-op when ``spec`` is unset — the base classes inherit a ``None``
    placeholder so ``as_view()`` itself doesn't trip.
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

    No-op when ``spec`` is unset — that means "use vanilla DRF". ``expected_kind``
    is the kind the view is shaped for (``LIST`` for :class:`SelectorListView`,
    ``RETRIEVE`` for :class:`SelectorRetrieveView`).
    """
    spec: SelectorSpec[Any, Any] | None = getattr(view_cls, "spec", None)
    if spec is None:
        return
    label = f"{view_cls.__name__}.spec"
    validate_selector_spec(spec, label=label, expected_kind=expected_kind)
    if expected_kind is SelectorKind.LIST:
        validate_filter_set_no_backend_conflict(view_cls, spec, label=label)
