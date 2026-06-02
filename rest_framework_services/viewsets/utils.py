"""Shared infrastructure for viewset mixins."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import MethodNotAllowed

from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.views.spec_validation import (
    is_overridden,
    validate_selector_spec,
    validate_service_spec,
)
from rest_framework_services.views.utils import layer_serializer_context

# Per-action context: ``has_instance`` controls signature validation +
# whether the mutation flow expects a fetched instance from ``get_object()``.
_MUTATION_ACTIONS: dict[str, dict[str, Any]] = {
    "create": {"has_instance": False},
    "update": {"has_instance": True},
    "destroy": {"has_instance": True},
}
_SELECTOR_ACTION_KIND: dict[str, SelectorKind] = {
    "list": SelectorKind.LIST,
    "retrieve": SelectorKind.RETRIEVE,
}


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
    elif action in _SELECTOR_ACTION_KIND and isinstance(spec, SelectorSpec):
        validate_selector_spec(spec, label=label, expected_kind=_SELECTOR_ACTION_KIND[action])


class _ActionSpecsMixin:
    """Declares the ``action_specs`` class attribute shared by all viewset mixins.

    All per-action mixins and :class:`ActionSerializerResolver` inherit from
    this so the attribute is defined in exactly one place. The
    :meth:`as_view` override below runs fail-fast signature validation on
    every entry in ``action_specs`` once per view at URL-wiring time.

    :meth:`get_permissions` honors ``spec.permission_classes`` on the entry
    for the current action when set. ``None`` (the default) inherits the
    view's class-level ``permission_classes``; an empty sequence means
    "no permissions" explicitly.
    """

    action_specs: ClassVar[Mapping[str, SelectorSpec | ServiceSpec]] = {}

    # Provided by ``GenericAPIView`` at runtime.
    action: str | None

    @classmethod
    def as_view(cls, *args: Any, **initkwargs: Any) -> Any:
        for action, spec in cls.action_specs.items():
            _validate_action_spec(cls, action, spec)
        return super().as_view(*args, **initkwargs)  # ty: ignore[unresolved-attribute]

    def get_serializer_context(self) -> dict[str, Any]:
        """Merge a SelectorSpec's output context provider into DRF's context.

        DRF's ``ListModelMixin`` / ``RetrieveModelMixin`` instantiate the
        response serializer via ``self.get_serializer(...)`` which calls
        ``self.get_serializer_context()``. Selector specs need their
        ``output_serializer_context`` surfaced through this path.

        Only applies when the current action's entry is a
        :class:`SelectorSpec`. Mutation actions handle context separately
        in :func:`dispatch_mutation_for_spec` (which passes ``context=``
        directly to the input and output serializers), so layering here
        would otherwise double-apply for mutations and bleed output
        context into the input direction.

        Layers (most specific last):

        1. ``super().get_serializer_context()`` — DRF default.
        2. ``self.get_<action>_output_serializer_context()`` — per-action
           override on the view, if defined.
        3. ``action_specs[self.action].output_serializer_context`` — per-spec
           callable, if set.

        The ``get_output_serializer_context`` *directional* hook is
        deliberately not consulted here: its default implementation on
        :class:`MutationFlowMixin` calls ``self.get_serializer_context()``,
        which would recurse into this override. Users who want a shared
        output-only context for selector list / retrieve should override
        ``get_serializer_context`` directly or use the per-action hook.
        """
        base: dict[str, Any] = dict(super().get_serializer_context())  # ty: ignore[unresolved-attribute]
        action: str | None = self.action
        entry: SelectorSpec | ServiceSpec | None = self.action_specs.get(action) if action else None
        if not isinstance(entry, SelectorSpec):
            return base
        # Offer the resolved data stashed by the selector list / retrieve
        # mixin under the action-appropriate name (``page`` for list,
        # ``instance`` for retrieve). Providers receive only what they declare.
        # A SelectorSpec entry is only ever the ``list`` or ``retrieve``
        # action, so the two arms are exhaustive.
        if _SELECTOR_ACTION_KIND.get(action) is SelectorKind.LIST:  # ty: ignore[invalid-argument-type]
            extras: dict[str, Any] = {"page": getattr(self, "_resolved_page", None)}
        else:
            extras = {"instance": getattr(self, "_resolved_instance", None)}
        return layer_serializer_context(
            base,
            self,
            self.request,  # ty: ignore[unresolved-attribute]
            direction_hook=None,
            action_hook=f"get_{action}_output_serializer_context",
            spec_provider=entry.output_serializer_context,
            extras=extras,
        )

    def get_permissions(self) -> list[Any]:
        spec: SelectorSpec | ServiceSpec | None = (
            self.action_specs.get(self.action) if self.action else None
        )
        if spec is None and self.action is not None:
            # Fall back to a spec attached by ``@service_action`` /
            # ``@selector_action`` on the bound handler so decorator-based
            # actions enforce their permissions without requiring the DRF
            # router (which would otherwise pass them via ``initkwargs``).
            handler: Any = getattr(self, self.action)
            spec = getattr(handler, "_service_spec", None) or getattr(
                handler, "_selector_spec", None
            )
        if spec is not None and spec.permission_classes is not None:
            return [permission() for permission in spec.permission_classes]
        return super().get_permissions()  # ty: ignore[unresolved-attribute]
