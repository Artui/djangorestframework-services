"""Shared infrastructure for viewset mixins."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, ClassVar, TypeVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import MethodNotAllowed, NotFound
from rest_framework.generics import GenericAPIView

from rest_framework_services.types.polymorphic_service_spec import PolymorphicServiceSpec
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.views.mutation.utils import _ServesRaisedSchema, mutation_target_source
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


_HandlerT = TypeVar("_HandlerT", bound=Callable[..., Any])


def dispatches_action_specs(handler: _HandlerT) -> _HandlerT:
    """Mark a viewset handler as this package's own ``action_specs`` dispatcher.

    Such a handler answers 405 when no spec serves its action, so OPTIONS can
    leave that method out rather than describe a body nobody accepts. A
    handler the view defines itself, even one calling ``super()``, is not
    marked: what it accepts is its own business, and OPTIONS describes it as
    DRF always has.
    """
    handler._dispatches_action_specs = True  # ty: ignore[unresolved-attribute]
    return handler


def resolves_action_serializers(method: _HandlerT) -> _HandlerT:
    """Mark ``get_serializer_class`` as this package's own resolution.

    OPTIONS leaves out a polymorphic method whose variants disagree only when
    the package's resolver is what would describe it. A view resolving
    serializers itself, through its own ``get_serializer_class`` (even one
    calling ``super()``) or ``get_serializer``, is described by that, as DRF
    always has.
    """
    method._resolves_action_serializers = True  # ty: ignore[unresolved-attribute]
    return method


def polymorphic_inputs_disagree(poly: PolymorphicServiceSpec) -> bool:
    """Whether a polymorphic spec's variants declare different input bodies."""
    return len({variant.input_serializer for variant in poly.specs.values()}) > 1


def resolve_action_spec(view: Any, action: str | None) -> ActionSpec | None:
    """Return the spec ``action`` routes to on ``view``, from either source.

    A spec reaches a viewset two ways: keyed in ``action_specs`` (read through
    [`resolve_action_spec_entry`][rest_framework_services.viewsets.utils.resolve_action_spec_entry],
    fallbacks included), or stamped on the bound handler by ``@service_action`` /
    ``@selector_action``. ``get_permissions`` and the OPTIONS metadata path both
    ask through here, so a hook that reads only one of the two cannot be written
    by accident again: it is how OPTIONS on a ``@service_action`` POST came to
    500 while its permissions resolved fine.

    ``action`` is taken as given and never translated. DRF's implicit
    ``"metadata"`` action has no handler and no entry, so it resolves to
    ``None``; the metadata path names the action it describes itself.

    The ``getattr`` default is load-bearing: no handler exists for an implicit
    action, and an ``AttributeError`` is not an ``APIException``, so
    ``handle_exception`` would re-raise it and every OPTIONS request (CORS
    preflights included) would 500 before permissions are evaluated. So is the
    ``action is None`` check, for a method the action map does not route: held
    by test_a_method_the_viewset_does_not_route_is_refused_rather_than_crashing.
    """
    spec = resolve_action_spec_entry(view.action_specs, action)
    if spec is not None or action is None:
        return spec
    handler: Any = getattr(view, action, None)
    return getattr(handler, "_service_spec", None) or getattr(handler, "_selector_spec", None)


def resolve_action_service_spec(
    action_specs: Mapping[str, ActionSpec],
    action: str,
    method: str,
    *,
    view: Any,
) -> ServiceSpec[Any, Any, Any]:
    """Pick a [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec]
    from ``action_specs`` for a mutation action.

    A
    [`PolymorphicServiceSpec`][rest_framework_services.types.polymorphic_service_spec.PolymorphicServiceSpec]
    entry is resolved to its chosen variant via the discriminator (memoized on ``view``
    for the request). Centralised so all three mutation viewset mixins share one error
    path."""
    entry = resolve_action_spec_entry(action_specs, action)
    if entry is None:
        raise MethodNotAllowed(method)
    if isinstance(entry, PolymorphicServiceSpec):
        return resolve_polymorphic_service_spec(entry, view=view, request=view.request)
    # ``as_view`` refuses this pairing at setup, so reaching it means the mapping
    # was built or swapped after the view was wired. Kept as the backstop.
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
    """Pick a [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec]
    from ``action_specs`` for a read action.

    ``None`` means the caller must fall back to vanilla DRF
    (``super().get_queryset()`` / ``super().get_object()``) — either the action is
    unconfigured or the spec opts out of selector dispatch (``selector=None``).
    """
    entry = action_specs.get(action)
    if entry is None:
        return None
    # As above: setup already refused this pairing, so this is the backstop for
    # a mapping that changed afterwards.
    if not isinstance(entry, SelectorSpec):
        raise ImproperlyConfigured(
            f"action_specs[{action!r}] must be a SelectorSpec, got "
            f"{type(entry).__name__}. "
            "Wrap the selector: SelectorSpec(selector=your_callable)."
        )
    if entry.selector is None:
        return None
    return entry


def _mutation_extras_are_permissive(view_cls: type, action: str) -> bool:
    """Whether a kwargs hook the validator cannot read into is in play."""
    return is_overridden(view_cls, MutationFlowMixin, "get_service_kwargs") or hasattr(
        view_cls, f"get_{action}_service_kwargs"
    )


def _validate_mutation_action_spec(
    view_cls: type, action: str, spec: object, *, label: str
) -> None:
    has_instance: bool = _MUTATION_ACTIONS[action]["has_instance"]
    permissive = _mutation_extras_are_permissive(view_cls, action)
    if isinstance(spec, PolymorphicServiceSpec):
        validate_polymorphic_service_spec(
            spec, label=label, has_instance=has_instance, permissive_extras=permissive
        )
        return
    if not isinstance(spec, ServiceSpec):
        raise ImproperlyConfigured(
            f"{label} must be a ServiceSpec, got {type(spec).__name__}. "
            "Use ServiceSpec(service=...) for write actions."
        )
    validate_service_spec(
        spec, label=label, has_instance=has_instance, permissive_extras=permissive
    )


def _validate_selector_action_spec(
    view_cls: type, action: str, spec: object, *, label: str
) -> None:
    if not isinstance(spec, SelectorSpec):
        raise ImproperlyConfigured(
            f"{label} must be a SelectorSpec, got {type(spec).__name__}. "
            "Wrap the selector: SelectorSpec(selector=your_callable)."
        )
    kind = _SELECTOR_ACTION_KIND[action]
    validate_selector_spec(spec, label=label, expected_kind=kind)
    if kind is SelectorKind.LIST:
        validate_filter_set_no_backend_conflict(view_cls, spec, label=label)


def _validate_custom_action_spec(view_cls: type, action: str, spec: object, *, label: str) -> None:
    """Validate an entry keyed on an action none of the mixins dispatch.

    A custom key is not dead configuration: ``get_permissions`` and
    [`ActionSerializerResolver`][rest_framework_services.viewsets.action_serializer_resolver.ActionSerializerResolver]
    both resolve by action name, so a ``@action``-decorated method takes its
    permissions and serializer from here. It used to be the one entry nothing
    validated at all.

    The kwargs chain is the same one a built-in action uses, so permissiveness
    is decided the same way. What the key cannot say is whether the action
    targets an instance, so ``has_instance`` is granted rather than guessed — a
    detail action would otherwise be refused for declaring the ``instance`` it
    does receive.
    """
    if isinstance(spec, PolymorphicServiceSpec):
        validate_polymorphic_service_spec(
            spec,
            label=label,
            has_instance=True,
            permissive_extras=_mutation_extras_are_permissive(view_cls, action),
        )
    elif isinstance(spec, ServiceSpec):
        validate_service_spec(
            spec,
            label=label,
            has_instance=True,
            permissive_extras=_mutation_extras_are_permissive(view_cls, action),
        )
    elif isinstance(spec, SelectorSpec):
        validate_selector_spec(spec, label=label)
    else:
        raise ImproperlyConfigured(
            f"{label} must be a ServiceSpec, PolymorphicServiceSpec or SelectorSpec, "
            f"got {type(spec).__name__}."
        )


def _validate_action_spec(view_cls: type, action: str, spec: object) -> None:
    """Run fail-fast validation for a single ``action_specs`` entry.

    Every entry is validated, whatever it is keyed on. The built-in action keys
    additionally get the type check the dispatch sites make at request time — a
    ``SelectorSpec`` under ``"create"``, or a ``ServiceSpec`` under
    ``"retrieve"``, is a mis-wiring that used to reach ``as_view()`` untouched
    and surface as an ``ImproperlyConfigured`` on the first request instead.
    """
    label = f"{view_cls.__name__}.action_specs[{action!r}]"
    if action in _MUTATION_ACTIONS:
        _validate_mutation_action_spec(view_cls, action, spec, label=label)
    elif action in _SELECTOR_ACTION_KIND:
        _validate_selector_action_spec(view_cls, action, spec, label=label)
    else:
        _validate_custom_action_spec(view_cls, action, spec, label=label)


class _ActionSpecsMixin(_ServesRaisedSchema):
    """Declares the ``action_specs`` class attribute shared by all viewset mixins.

    All per-action mixins and
    [`ActionSerializerResolver`][rest_framework_services.viewsets.action_serializer_resolver.ActionSerializerResolver]
    inherit from this so the attribute is defined in exactly one place. ``as_view`` runs
    fail-fast spec validation once per view at URL-wiring time.

    In ``get_permissions``, a spec's ``permission_classes`` of ``None`` means
    inherit the view's; an empty sequence means "no permissions" explicitly.
    """

    action_specs: ClassVar[Mapping[str, ActionSpec]] = {}
    # Set by ``options()`` on the instance answering an OPTIONS request; read
    # through ``_describing_for_options``. The class default is what a view
    # built any other way sees: a real request, a schema generator, a test.
    _answering_options: bool = False

    # Provided by ``GenericAPIView`` / ``ViewSetMixin`` at runtime.
    action: str | None
    action_map: dict[str, str]
    kwargs: dict[str, Any]
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

        Restricted to
        [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec]
        entries: mutations already apply their context inside
        ``dispatch_mutation_for_spec``, so layering here would double-apply it and bleed
        output context into the input direction.

        The ``get_output_serializer_context`` *directional* hook is deliberately not
        consulted —
        [`MutationFlowMixin`][rest_framework_services.views.mutation.mutation_flow_mixin.MutationFlowMixin]'s
        default implementation calls ``self.get_serializer_context()`` and would recurse
        into this override."""
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

    def _described_action_spec(self) -> ActionSpec | None:
        """The spec behind the method an OPTIONS response is describing.

        DRF answers OPTIONS under the implicit ``"metadata"`` action, and its
        ``SimpleMetadata.determine_actions`` swaps in a clone of the request
        carrying each POST / PUT it describes. So ``self.action`` stays
        ``"metadata"`` while ``request.method`` names the method, and the
        action that method routes to is read from the viewset's
        ``action_map``.

        Only the metadata path asks this. ``get_permissions`` deliberately does
        not: OPTIONS answers to the view's own permissions, never to those of
        an action it describes, which is pinned by
        test_a_locked_down_action_still_has_its_body_described.
        """
        return resolve_action_spec(self, self._described_action())

    def _described_action(self) -> str:
        # ``""`` rather than ``None`` for a method the action map does not
        # route: every lookup keyed on it then answers "nothing" without a
        # branch of its own.
        return self.action_map.get(self.request.method.lower(), "")

    def options(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """Answer OPTIONS as DRF does, marking the view as describing itself.

        DRF sets ``self.action = "metadata"`` for an OPTIONS request, but also
        for a real request routed to a view's own action named ``metadata``.
        The flag set here is what tells the two apart. It is set on the view
        instance, so it holds through DRF's per-method probe, which swaps in a
        clone of the request on that same instance. A view overriding
        ``options()`` without calling ``super()`` gets DRF's own answers.
        """
        self._answering_options = True
        return super().options(request, *args, **kwargs)  # ty: ignore[unresolved-attribute]

    def _describing_for_options(self) -> bool:
        """Whether this is DRF's OPTIONS metadata describing the view.

        The one test every OPTIONS-only behaviour here and in
        ``ActionSerializerResolver`` asks. Both conditions are needed. The flag
        alone stays set after the OPTIONS answer is built, while the browsable
        API renders that response's forms under the action each one posts to,
        held by test_the_browsable_options_page_renders_forms_as_before. The
        action alone is also ``"metadata"`` for a real request to a view's own
        action of that name, held by the five tests of a real action named
        ``metadata`` in test_options_metadata.py.
        """
        return self.action == "metadata" and self._answering_options

    def check_permissions(self, request: Any) -> None:
        """Leave out of OPTIONS a method that cannot be described.

        DRF's metadata calls ``check_permissions`` first for every POST / PUT it
        describes, inside the ``try`` that drops a method on an
        ``APIException``. So raising ``MethodNotAllowed`` here leaves the method
        out of ``actions`` (the ``Allow`` header still lists it), before
        ``get_object()`` or ``get_serializer()`` are asked anything. Two cases:

        - the method routes to one of this package's own spec-dispatching
          handlers and no spec serves it, as when a router maps PUT to
          ``update`` on a viewset with no ``update`` spec. A real request
          answers 405 there, so the method is left out even where a
          ``serializer_class`` or a serializer override would describe it
          (the ``*-behind-a-get-serializer*`` rows of
          test_a_method_no_spec_serves_is_left_out_and_still_refused). A
          handler the view defines itself carries no
          marker and is described as DRF always has;
        - a polymorphic spec whose variants accept different bodies, where the
          package's own resolver would describe it: no ``serializer_class``,
          and neither ``get_serializer_class`` nor ``get_serializer`` is the
          view's own. Which body applies is the discriminator's call on a body
          OPTIONS does not have.

        Only while describing the view for OPTIONS: every other request,
        including a real one to an action named ``metadata``, reaches its
        handler as before. The view's own permission classes are checked first,
        so a view that refuses OPTIONS still refuses it. The OPTIONS request's
        own permission check runs before the handler that sets the flag, so it
        is not affected either.

        Each condition is held by a test that fails without it: the OPTIONS
        test by test_a_polymorphic_spec_keyed_metadata_beside_a_plain_action_is_served,
        no spec by every described row of
        test_options_describes_every_spec_only_shape, the handler marker by
        test_a_views_own_handler_without_a_spec_is_described, disagreeing
        variants by the ``polymorphic-shared-input`` row, the missing
        ``serializer_class`` by
        test_a_polymorphic_body_falls_back_to_the_views_serializer_class, the
        resolver marker by
        test_a_polymorphic_post_is_described_by_a_get_serializer_class_override
        and ``get_serializer`` by
        test_a_polymorphic_post_is_described_by_a_get_serializer_override.
        """
        super().check_permissions(request)  # ty: ignore[unresolved-attribute]
        if not self._describing_for_options():
            return
        action = self._described_action()
        spec = resolve_action_spec(self, action)
        unserved = spec is None and getattr(
            getattr(self, action, None), "_dispatches_action_specs", False
        )
        ambiguous = (
            isinstance(spec, PolymorphicServiceSpec)
            and polymorphic_inputs_disagree(spec)
            and getattr(self, "serializer_class", None) is None
            and getattr(
                getattr(self, "get_serializer_class", None),
                "_resolves_action_serializers",
                False,
            )
            and not is_overridden(type(self), GenericAPIView, "get_serializer")
        )
        if unserved or ambiguous:
            raise MethodNotAllowed(request.method)

    def get_object(self) -> Any:
        if self._metadata_probe_has_no_row():
            return None
        return super().get_object()  # ty: ignore[unresolved-attribute]

    def _metadata_probe_has_no_row(self) -> bool:
        """Whether DRF's OPTIONS probe is describing a PUT that targets no row.

        DRF's metadata calls ``get_object()`` for every PUT it describes, as an
        object-permission probe. Two PUTs have no row to probe: a non-detail
        action (``detail=False``), and a bulk PUT (``many=True`` or a
        ``collection_selector_spec``), which never fetches a row or checks an
        object permission when sent. Routed, as they usually are, on a URL with
        no lookup kwarg, the probe could only fail on configuration (the
        missing kwarg, or a missing ``queryset``) and end OPTIONS in a 500.
        Skipping it there describes the PUT, as DRF does for a view with no
        ``get_object()``. Where the URL does carry the kwarg, the probe runs as
        it always has.

        Asked first by every ``get_object()`` in the viewset bases, the retrieve
        selector's included, since that one fails the same way without a
        lookup kwarg. A handler with no ``detail`` attribute, such as the
        built-in ``update``, is a detail action. Each condition is held by a
        test that fails without it: the OPTIONS test by
        test_a_non_detail_put_named_metadata_still_looks_its_row_up, the lookup
        kwarg by test_a_bulk_put_with_a_lookup_kwarg_is_still_probed, the
        ``detail`` default by the ``put-through-get-object-on-a-list-route``
        row of test_what_options_still_cannot_describe, the bulk source by
        test_a_bulk_put_on_a_list_route_is_described, the ``ServiceSpec`` check
        by the ``polymorphic-put-on-a-list-route`` row of the same test, and
        the ``detail=False`` check and the two call sites by the
        ``non-detail-put-*`` rows of test_options_describes_a_non_detail_put.
        """
        if not self._describing_for_options():
            return False
        lookup = getattr(self, "lookup_url_kwarg", None) or getattr(self, "lookup_field", "pk")
        if lookup in self.kwargs:
            return False
        action = self._described_action()
        if getattr(getattr(self, action, None), "detail", True) is False:
            return True
        spec = resolve_action_spec(self, action)
        return isinstance(spec, ServiceSpec) and mutation_target_source(spec) == "bulk"

    def get_queryset(self) -> Any:
        """Leave out a PUT that DRF's OPTIONS probe cannot look up.

        DRF's metadata calls ``get_object()`` for every PUT it describes, and
        that lookup reaches this method, DRF's own, only once every other row
        source (a ``get_queryset()`` override, a list selector) has deferred.
        With no ``queryset`` either, DRF raises a configuration
        ``AssertionError`` and OPTIONS ends in a 500. That is a fair answer for
        a PUT that looks its row up through ``get_object()``, which fails the
        same way when sent. A PUT whose target comes from an instance selector,
        or a bulk PUT on a URL carrying a lookup kwarg (one without is never
        probed), never makes that lookup, though, so the view has no reason to
        carry a ``queryset``. For that PUT, ``NotFound`` is raised instead and
        DRF leaves it out of ``actions``. A real instance-selector PUT checks
        object permissions on the selector's row, which OPTIONS does not
        fetch. Wherever a row source exists, the probe runs exactly as before;
        the instance selector is never run.

        Each condition is held by a test that fails without it: the OPTIONS
        test by test_a_real_action_named_metadata_still_needs_a_queryset, the
        missing ``queryset`` by the ``put-instance-selector-with-queryset`` row
        of test_options_describes_every_spec_only_shape, the ``ServiceSpec`` and
        target-source conditions by test_a_put_options_cannot_look_up_still_raises,
        and its bulk half by the ``put-bulk-no-queryset`` row.
        """
        if self._describing_for_options() and getattr(self, "queryset", None) is None:
            spec = self._described_action_spec()
            if isinstance(spec, ServiceSpec) and mutation_target_source(spec) != "get_object":
                raise NotFound
        return super().get_queryset()  # ty: ignore[unresolved-attribute]

    def get_permissions(self) -> list[Any]:
        # Same resolution as dispatch: the ``"partial_update"`` -> ``"update"``
        # fallback, so PATCH enforces an ``"update"``-keyed spec's
        # ``permission_classes``, then the spec ``@service_action`` /
        # ``@selector_action`` stamped on the handler, so decorator-based
        # actions enforce permissions without the DRF router's ``initkwargs``.
        spec = resolve_action_spec(self, self.action)
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
        # union / require_identical. Deduplicated by equality, in first-seen
        # order. Not by hashing: DRF 3.15.0 and 3.15.1 give a composed operand
        # (``IsAuthenticated | AllowAny``) ``__eq__`` without ``__hash__``, so it
        # cannot be a dict key there, and a view-level ``permission_classes`` can
        # hold one for every variant that declares none of its own.
        classes: list[Any] = []
        for variant in poly.specs.values():
            variant_classes = (
                variant.permission_classes
                if variant.permission_classes is not None
                else self.permission_classes
            )
            for permission_class in variant_classes:
                if permission_class not in classes:
                    classes.append(permission_class)
        return [permission_class() for permission_class in classes]
