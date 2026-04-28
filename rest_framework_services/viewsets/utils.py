"""Shared infrastructure for viewset mixins."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import MethodNotAllowed

from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.views.spec_validation import (
    is_overridden,
    validate_selector_spec,
    validate_service_spec,
)

# Per-action context: ``has_instance`` controls signature validation +
# whether the mutation flow expects a fetched instance from ``get_object()``.
_MUTATION_ACTIONS: dict[str, dict[str, Any]] = {
    "create": {"has_instance": False},
    "update": {"has_instance": True},
    "destroy": {"has_instance": True},
}
_SELECTOR_ACTIONS = {"list", "retrieve"}


def resolve_action_service_spec(
    action_specs: Mapping[str, SelectorSpec | ServiceSpec],
    action: str,
    method: str,
) -> ServiceSpec[Any, Any, Any]:
    """Pick a :class:`ServiceSpec` from ``action_specs`` for a mutation action.

    Raises :exc:`MethodNotAllowed` when the action is not configured and
    :exc:`ImproperlyConfigured` when the entry is the wrong spec type.
    Centralised so all three mutation viewset mixins share one error path.
    """
    entry = action_specs.get(action)
    if entry is None:
        raise MethodNotAllowed(method)
    if not isinstance(entry, ServiceSpec):
        raise ImproperlyConfigured(
            f"action_specs[{action!r}] must be a ServiceSpec, got "
            f"{type(entry).__name__}. "
            "Use ServiceSpec(service=...) for write actions."
        )
    return entry


def resolve_action_selector_spec(
    action_specs: Mapping[str, SelectorSpec | ServiceSpec],
    action: str,
) -> SelectorSpec[Any, Any] | None:
    """Pick a :class:`SelectorSpec` from ``action_specs`` for a read action.

    Returns ``None`` when the action is unconfigured or the spec opts out
    of selector dispatch (``selector=None``); the caller is expected to
    fall back to vanilla DRF (``super().get_queryset()`` /
    ``super().get_object()``). Raises :exc:`ImproperlyConfigured` when the
    entry is the wrong spec type.
    """
    entry = action_specs.get(action)
    if entry is None:
        return None
    if not isinstance(entry, SelectorSpec):
        raise ImproperlyConfigured(
            f"action_specs[{action!r}] must be a SelectorSpec, got "
            f"{type(entry).__name__}. "
            "Wrap the selector: SelectorSpec(selector=your_callable)."
        )
    if entry.selector is None:
        return None
    return entry


def _validate_action_spec(view_cls: type, action: str, spec: object) -> None:
    """Run signature validation for a single ``action_specs`` entry."""
    label = f"{view_cls.__name__}.action_specs[{action!r}]"
    if action in _MUTATION_ACTIONS and isinstance(spec, ServiceSpec):
        permissive = is_overridden(view_cls, MutationFlowMixin, "get_service_kwargs") or hasattr(
            view_cls, f"get_{action}_service_kwargs"
        )
        validate_service_spec(
            spec,
            label=label,
            has_instance=_MUTATION_ACTIONS[action]["has_instance"],
            permissive_extras=permissive,
        )
    elif action in _SELECTOR_ACTIONS and isinstance(spec, SelectorSpec):
        validate_selector_spec(spec, label=label)


class _ActionSpecsMixin:
    """Declares the ``action_specs`` class attribute shared by all viewset mixins.

    All per-action mixins and :class:`ActionSerializerResolver` inherit from
    this so the attribute is defined in exactly one place. The
    :meth:`as_view` override below runs fail-fast signature validation on
    every entry in ``action_specs`` once per view at URL-wiring time.
    """

    action_specs: ClassVar[Mapping[str, SelectorSpec | ServiceSpec]] = {}

    @classmethod
    def as_view(cls, *args: Any, **initkwargs: Any) -> Any:
        for action, spec in cls.action_specs.items():
            _validate_action_spec(cls, action, spec)
        return super().as_view(*args, **initkwargs)  # ty: ignore[unresolved-attribute]
