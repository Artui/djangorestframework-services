"""Shared infrastructure for viewset mixins."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import MethodNotAllowed

from rest_framework_services.types.polymorphic_service_spec import PolymorphicServiceSpec
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.views.spec_validation import (
    is_overridden,
    validate_filter_set_no_backend_conflict,
    validate_polymorphic_service_spec,
    validate_selector_spec,
    validate_service_spec,
)
from rest_framework_services.views.utils import layer_serializer_context
from rest_framework_services.viewsets.resolve_polymorphic_service_spec import (
    resolve_polymorphic_service_spec,
)

ActionSpec = SelectorSpec | ServiceSpec | PolymorphicServiceSpec

# ``has_instance`` drives signature validation and whether the mutation flow
# expects a fetched instance from ``get_object()``.
_MUTATION_ACTIONS: dict[str, dict[str, Any]] = {
    "create": {"has_instance": False},
    "update": {"has_instance": True},
    "partial_update": {"has_instance": True},
    "destroy": {"has_instance": True},
}
_SELECTOR_ACTION_KIND: dict[str, SelectorKind] = {
    "list": SelectorKind.LIST,
    "retrieve": SelectorKind.RETRIEVE,
}

# PATCH and PUT share one spec unless a dedicated ``"partial_update"`` entry
# exists. Must be applied at every resolution site (see
# ``resolve_action_spec_entry``).
_ACTION_SPEC_FALLBACKS: dict[str, str] = {
    "partial_update": "update",
}


def resolve_action_spec_entry(
    action_specs: Mapping[str, ActionSpec],
    action: str | None,
) -> ActionSpec | None:
    """Return the ``action_specs`` entry for ``action``, following fallbacks.

    Single source of truth for the action→spec-key chain: dispatch, permission
    resolution and serializer resolution must all route through here, or they
    disagree on the key (an ``"update"``-keyed spec's ``permission_classes``
    silently not applying under PATCH).
    """
    if action is None:
        return None
    entry = action_specs.get(action)
    if entry is not None:
        return entry
    fallback = _ACTION_SPEC_FALLBACKS.get(action)
    if fallback is not None:
        return action_specs.get(fallback)
    return None


def resolve_action_service_spec(
    action_specs: Mapping[str, ActionSpec],
    action: str,
    method: str,
    *,
    view: Any,
) -> ServiceSpec[Any, Any, Any]:
    """Pick a [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] from ``action_specs`` for a mutation action.

    A [`PolymorphicServiceSpec`][rest_framework_services.types.polymorphic_service_spec.PolymorphicServiceSpec] entry is resolved to its chosen variant via
    the discriminator (memoized on ``view`` for the request). Centralised so all
    three mutation viewset mixins share one error path.
    """
    entry = resolve_action_spec_entry(action_specs, action)
    if entry is None:
        raise MethodNotAllowed(method)
    if isinstance(entry, PolymorphicServiceSpec):
        return resolve_polymorphic_service_spec(entry, view=view, request=view.request)
    if not isinstance(entry, ServiceSpec):
        raise ImproperlyConfigured(
            f"action_specs[{action!r}] must be a ServiceSpec, got "
            f"{type(entry).__name__}. "
            "Use ServiceSpec(service=...) for write actions."
        )
    return entry


def resolve_action_selector_spec(
    action_specs: Mapping[str, ActionSpec],
    action: str,
) -> SelectorSpec[Any, Any] | None:
    """Pick a [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] from ``action_specs`` for a read action.

    ``None`` means the caller must fall back to vanilla DRF
    (``super().get_queryset()`` / ``super().get_object()``) — either the action is
    unconfigured or the spec opts out of selector dispatch (``selector=None``).
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
    if action in _MUTATION_ACTIONS and isinstance(spec, PolymorphicServiceSpec):
        permissive = is_overridden(view_cls, MutationFlowMixin, "get_service_kwargs") or hasattr(
            view_cls, f"get_{action}_service_kwargs"
        )
        validate_polymorphic_service_spec(
            spec,
            label=label,
            has_instance=_MUTATION_ACTIONS[action]["has_instance"],
            permissive_extras=permissive,
        )
    elif action in _MUTATION_ACTIONS and isinstance(spec, ServiceSpec):
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
        kind = _SELECTOR_ACTION_KIND[action]
        validate_selector_spec(spec, label=label, expected_kind=kind)
        if kind is SelectorKind.LIST:
            validate_filter_set_no_backend_conflict(view_cls, spec, label=label)


class _ActionSpecsMixin:
    """Declares the ``action_specs`` class attribute shared by all viewset mixins.

    All per-action mixins and [`ActionSerializerResolver`][rest_framework_services.viewsets.action_serializer_resolver.ActionSerializerResolver] inherit from this
    so the attribute is defined in exactly one place. ``as_view`` runs
    fail-fast spec validation once per view at URL-wiring time.

    In ``get_permissions``, a spec's ``permission_classes`` of ``None`` means
    inherit the view's; an empty sequence means "no permissions" explicitly.
    """

    action_specs: ClassVar[Mapping[str, ActionSpec]] = {}

    # Provided by ``GenericAPIView`` at runtime.
    action: str | None
    # DRF's class-level permission classes, consulted for the ``union`` strategy.
    permission_classes: Any
    request: Any

    @classmethod
    def as_view(cls, *args: Any, **initkwargs: Any) -> Any:
        for action, spec in cls.action_specs.items():
            _validate_action_spec(cls, action, spec)
        return super().as_view(*args, **initkwargs)  # ty: ignore[unresolved-attribute]

    def get_serializer_context(self) -> dict[str, Any]:
        """Merge a SelectorSpec's output context provider into DRF's context.

        DRF's ``ListModelMixin`` / ``RetrieveModelMixin`` reach the response
        serializer's context only through here, so a selector spec's
        ``output_serializer_context`` has to be layered on this path.

        Restricted to [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] entries: mutations already apply
        their context inside ``dispatch_mutation_for_spec``, so layering here
        would double-apply it and bleed output context into the input direction.

        The ``get_output_serializer_context`` *directional* hook is deliberately
        not consulted — [`MutationFlowMixin`][rest_framework_services.views.mutation.mutation_flow_mixin.MutationFlowMixin]'s default implementation calls
        ``self.get_serializer_context()`` and would recurse into this override.
        """
        base: dict[str, Any] = dict(super().get_serializer_context())  # ty: ignore[unresolved-attribute]
        action: str | None = self.action
        entry: ActionSpec | None = resolve_action_spec_entry(self.action_specs, action)
        if not isinstance(entry, SelectorSpec):
            return base
        # Names the data stashed by the selector list / retrieve mixin the way
        # the action's providers expect it. A SelectorSpec entry is only ever the
        # ``list`` or ``retrieve`` action, so the two arms are exhaustive.
        if _SELECTOR_ACTION_KIND.get(action) is SelectorKind.LIST:
            extras: dict[str, Any] = {"page": getattr(self, "_resolved_page", None)}
        else:
            extras = {"instance": getattr(self, "_resolved_instance", None)}
        return layer_serializer_context(
            base,
            self,
            self.request,
            direction_hook=None,
            action_hook=f"get_{action}_output_serializer_context",
            spec_provider=entry.output_serializer_context,
            extras=extras,
        )

    def get_permissions(self) -> list[Any]:
        # Same fallback chain as dispatch, so PATCH enforces an
        # ``"update"``-keyed spec's ``permission_classes``.
        spec: ActionSpec | None = resolve_action_spec_entry(self.action_specs, self.action)
        if spec is None and self.action is not None:
            # Falls back to a spec stamped on the bound handler by
            # ``@service_action`` / ``@selector_action``, so decorator-based
            # actions enforce permissions without the DRF router's ``initkwargs``.
            #
            # The ``getattr`` default is load-bearing: DRF sets
            # ``self.action = "metadata"`` for OPTIONS and no such handler
            # exists. An ``AttributeError`` here is not an ``APIException``, so
            # ``handle_exception`` re-raises it and every OPTIONS request (CORS
            # preflights included) 500s before permissions are evaluated.
            handler: Any = getattr(self, self.action, None)
            spec = getattr(handler, "_service_spec", None) or getattr(
                handler, "_selector_spec", None
            )
        if isinstance(spec, PolymorphicServiceSpec):
            return self._polymorphic_permissions(spec)
        if spec is not None and spec.permission_classes is not None:
            return [permission() for permission in spec.permission_classes]
        return super().get_permissions()  # ty: ignore[unresolved-attribute]

    def _polymorphic_permissions(self, poly: PolymorphicServiceSpec) -> list[Any]:
        """Permissions for a polymorphic action, per its ``permission_strategy``.

        ``discriminate`` resolves the chosen variant (reading the raw body) and
        applies only its permissions; ``union`` / ``require_identical`` apply the
        deduplicated union of every variant's classes.
        """
        if poly.permission_strategy == "discriminate":
            chosen = resolve_polymorphic_service_spec(poly, view=self, request=self.request)
            if chosen.permission_classes is not None:
                return [permission() for permission in chosen.permission_classes]
            return super().get_permissions()  # ty: ignore[unresolved-attribute]
        # union / require_identical. A dict is the dedup: it preserves first-seen
        # order, which a set would not.
        classes: dict[type, None] = {}
        for variant in poly.specs.values():
            variant_classes = (
                variant.permission_classes
                if variant.permission_classes is not None
                else self.permission_classes
            )
            for permission_class in variant_classes:
                classes.setdefault(permission_class, None)
        return [permission_class() for permission_class in classes]
