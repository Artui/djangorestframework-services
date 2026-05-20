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

from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

# Keys the framework injects automatically into the pool.
_FRAMEWORK_KEY_DATA = "data"
_FRAMEWORK_KEY_INSTANCE = "instance"
_FRAMEWORK_KEY_RESULT = "result"


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
            "is not an `output_selector`. Remove `result` from the signature or "
            "attach the callable to `ServiceSpec.output_selector`."
        )

    if permissive_extras or spec_kwargs is not None:
        # User is plugging in their own kwargs source; the framework cannot
        # statically know what they provide, so don't second-guess them.
        return

    known: set[str] = {"request", "user"}
    if has_data:
        known.add(_FRAMEWORK_KEY_DATA)
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


def _validate_queryset_shaping(
    spec: SelectorSpec[Any, Any],
    *,
    label: str,
) -> None:
    """Raise :exc:`ImproperlyConfigured` when shaping is set without a selector.

    ``select_related`` / ``prefetch_related`` / ``annotations`` /
    ``extend_queryset`` only run inside :func:`dispatch_selector_for_spec`,
    which is skipped when ``spec.selector is None``. Catching the misuse
    at ``as_view()`` time beats a silent no-op at request time.
    """
    if spec.selector is not None:
        return
    if (
        spec.select_related is not None
        or spec.prefetch_related is not None
        or spec.annotations is not None
        or spec.extend_queryset is not None
    ):
        raise ImproperlyConfigured(
            f"{label}: select_related / prefetch_related / annotations / "
            "extend_queryset are set but `selector` is not. Set a selector or "
            "drop the shaping fields — they only run when the spec's selector "
            "dispatches."
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


def validate_service_spec(
    spec: ServiceSpec[Any, Any, Any],
    *,
    label: str,
    has_instance: bool,
    permissive_extras: bool,
) -> None:
    """Validate a :class:`ServiceSpec`'s ``service`` and ``output_selector``.

    Shared between standalone mutation views, viewset mixins, and
    ``@service_action``. ``has_instance`` is fixed by the action context
    (``False`` for create, ``True`` for update / destroy / detail actions).
    """
    _validate_permission_classes(spec.permission_classes, label=label)
    validate_callable_signature(
        spec.service,
        spec_label=label,
        has_data=spec.input_serializer is not None,
        has_instance=has_instance,
        has_result=False,
        spec_kwargs=spec.kwargs,
        permissive_extras=permissive_extras,
    )
    if spec.output_selector is not None:
        validate_callable_signature(
            spec.output_selector,
            spec_label=f"{label}.output_selector",
            has_data=spec.input_serializer is not None,
            has_instance=has_instance,
            has_result=True,
            spec_kwargs=spec.kwargs,
            permissive_extras=permissive_extras,
        )


def validate_selector_spec(
    spec: SelectorSpec[Any, Any],
    *,
    label: str,
) -> None:
    """Validate a :class:`SelectorSpec`'s ``selector``.

    Selectors are always permissive on extras (URL kwargs and
    ``get_selector_kwargs`` are dynamic), so the only fatal misuses are
    requesting framework-only keys that don't exist in the selector pool
    (``data``, ``instance``, ``result``).
    """
    _validate_permission_classes(spec.permission_classes, label=label)
    _validate_queryset_shaping(spec, label=label)
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


def validate_selector_view_spec(view_cls: type) -> None:
    """Validate ``view_cls.spec`` on a standalone selector view.

    No-op when ``spec`` is unset or carries no ``selector`` (the spec then
    means "use vanilla DRF" and there is nothing to validate).
    """
    spec: SelectorSpec[Any, Any] | None = getattr(view_cls, "spec", None)
    if spec is None:
        return
    validate_selector_spec(spec, label=f"{view_cls.__name__}.spec")
