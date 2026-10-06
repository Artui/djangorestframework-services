"""``@service_action`` decorator — DRF ``@action`` plus service plumbing."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework import status as drf_status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.types.polymorphic_service_spec import PolymorphicServiceSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.map_service_error import (
    _AdditionalInputAPIException,
)
from rest_framework_services.views.mutation.utils import (
    dispatch_mutation_for_spec,
    resolve_mutation_instance,
    restore_raised_schema,
)
from rest_framework_services.views.spec_validation import (
    validate_polymorphic_service_spec,
    validate_service_spec,
)
from rest_framework_services.viewsets.resolve_polymorphic_service_spec import (
    resolve_polymorphic_service_spec,
)
from rest_framework_services.viewsets.utils import _ActionSpecsMixin


def _restore_schema_when_handled(view: Any) -> None:
    """Have the exception handler this request's view runs put the raised schema back.

    The restore is otherwise a ``handle_exception`` override on drfs' bases,
    which a decorated method on DRF's own viewsets never reaches, and which
    DRF's own method shadows where a drfs base is listed after
    ``GenericViewSet``. The decorator owns no class, but it runs on the view
    instance ``APIView.dispatch`` is about to hand the error to.

    So that instance's ``get_exception_handler`` is wrapped, for that request,
    and the error is re-raised rather than answered here.
    ``APIView.handle_exception`` asks for the handler as it always does, and gets
    one that runs the handler it would have got and then restores the schema on
    the response that built. Everything else follows from where that is:

    - The handler runs once and sees DRF's ``ErrorDetail`` leaves, and one that
      declines the error by returning ``None`` still returns ``None``, so DRF
      raises the error once, as on any other view. Answering from inside the
      action instead would hand a declined error back to ``dispatch``, which
      runs the handler a second time. Held by
      test_a_handler_declining_the_error_runs_once_on_any_viewset.
    - The restore runs inside ``APIView.handle_exception``, beneath every
      ``handle_exception`` in the class's chain, so an override of it has the
      last word on the body on every viewset. Wrapping ``handle_exception``
      itself ran the restore after the whole chain and undid what an override
      wrote to ``schema``. Held by
      test_an_overriding_handle_exception_has_the_last_word_on_every_route.
    - On drfs' bases ``_ServesRaisedSchema`` restores again on the way out of
      ``APIView.handle_exception``. No ``handle_exception`` of drfs' runs
      between the two, and each writes the schema as raised, so the second
      leaves the body as the first left it.

    The restore inside the handler is what serves the schema as raised where
    the class's chain never reaches ``_ServesRaisedSchema``. Held by
    test_service_action_serves_the_schema_as_raised_on_any_viewset, whose
    ``GenericViewSet``, ``ViewSet``, ``GenericViewSet, ActionSerializerResolver``
    and ``GenericViewSet, PolymorphicServiceSpec`` cases are served stringified
    without it.
    """
    get_exception_handler: Callable[[], Callable[..., Any]] = view.get_exception_handler

    def restoring_exception_handler() -> Callable[[Exception, dict[str, Any]], Response | None]:
        # Asked through the bound method when ``handle_exception`` asks, as DRF
        # asks it, so a ``get_exception_handler`` of the viewset's own still
        # chooses the handler. Held by
        # test_a_viewsets_own_exception_handler_still_answers_on_any_viewset.
        handler: Callable[..., Any] = get_exception_handler()

        def restoring(exc: Exception, context: dict[str, Any]) -> Response | None:
            return restore_raised_schema(exc, handler(exc, context))

        return restoring

    view.get_exception_handler = restoring_exception_handler


def service_action(
    spec: ServiceSpec | PolymorphicServiceSpec,
    *,
    detail: bool = False,
    methods: list[str] | None = None,
    url_path: str | None = None,
    url_name: str | None = None,
    **action_kwargs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Wrap a viewset method as a service-backed custom action.

    The decorated method's body is *not* executed — the decorator supplies
    the handler. The method exists so that ``@service_action`` can attach
    DRF ``@action`` metadata and pick up the action name from ``__name__``.

    Pass a [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] (or a
    [`PolymorphicServiceSpec`][rest_framework_services.types.polymorphic_service_spec.PolymorphicServiceSpec]
    for one action accepting several payload shapes) for the service wiring. ``detail``,
    ``methods``, ``url_path``, ``url_name``, and any extra ``**action_kwargs`` are
    forwarded to DRF's ``@action``.

    A plain ``ServiceSpec`` forwards its ``permission_classes`` into DRF's
    ``@action(permission_classes=...)``, so it is enforced on any viewset. A
    [`PolymorphicServiceSpec`][rest_framework_services.types.polymorphic_service_spec.PolymorphicServiceSpec]
    has no single list to forward — which list applies depends on the strategy,
    and under ``"discriminate"`` on the body — so its per-variant
    ``permission_classes`` are enforced by ``_ActionSpecsMixin.get_permissions``,
    which reads the spec stashed on the handler. That makes the mixin a
    **requirement**, not a convenience: on a viewset without it, DRF's stock
    ``get_permissions`` would apply the view's defaults and every variant rule
    would go unchecked. The decorated class is unknown at decoration time, so
    the dependency is checked on the first request through the action and the
    request is refused rather than served unguarded. Compose ``ServiceViewSet``
    (or any mixin from this package) and the check never fires.

    An [`AdditionalInputRequired`][rest_framework_services.exceptions.additional_input_required.AdditionalInputRequired]
    answers ``422`` with its ``schema`` as raised on any viewset, DRF's own
    included. The restore runs inside the exception handler the view asks for,
    so the configured ``EXCEPTION_HANDLER`` runs once, and a ``handle_exception``
    override on the viewset still has the last word on the body.
    """
    drf_kwargs: dict[str, Any] = {"detail": detail}
    if methods is not None:
        drf_kwargs["methods"] = methods
    if url_path is not None:
        drf_kwargs["url_path"] = url_path
    if url_name is not None:
        drf_kwargs["url_name"] = url_name
    if isinstance(spec, ServiceSpec) and spec.permission_classes is not None:
        drf_kwargs["permission_classes"] = list(spec.permission_classes)
    drf_kwargs.update(action_kwargs)
    # A variant list can only be enforced through ``_ActionSpecsMixin``. When no
    # variant declares one there is nothing to lose: DRF's own lookup and the
    # mixin's union both end at the view's class-level permissions.
    needs_action_specs_mixin = isinstance(spec, PolymorphicServiceSpec) and any(
        variant.permission_classes is not None for variant in spec.specs.values()
    )

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn_label = getattr(fn, "__qualname__", repr(fn))
        # The viewset class is unknown at decoration time, so any
        # ``get_<action>_service_kwargs`` / ``get_service_kwargs`` it may
        # carry has to be assumed permissive.
        if isinstance(spec, PolymorphicServiceSpec):
            validate_polymorphic_service_spec(
                spec,
                label=f"@service_action {fn_label}",
                has_instance=detail,
                permissive_extras=True,
            )
        else:
            validate_service_spec(
                spec,
                label=f"@service_action {fn_label}",
                has_instance=detail,
                permissive_extras=True,
            )

        @functools.wraps(fn)
        def handler(self: Any, request: Request, *args: Any, **kwargs: Any) -> Response:
            if needs_action_specs_mixin and not isinstance(self, _ActionSpecsMixin):
                raise ImproperlyConfigured(
                    f"@service_action {fn_label}: the variants declare "
                    "`permission_classes`, which only `_ActionSpecsMixin.get_permissions` "
                    f"enforces — and {type(self).__name__} does not provide it, so DRF "
                    "applied the view's default permissions and no variant rule ran. "
                    "Compose the viewset with ServiceViewSet (or any mixin from this "
                    "package), or move the rule to the view's `permission_classes`."
                )
            try:
                # A polymorphic spec resolves to its chosen variant first (memoized
                # for the request), then dispatches exactly as a plain spec. Inside
                # the ``try`` because the discriminator's errors are mapped too.
                concrete: ServiceSpec = (
                    resolve_polymorphic_service_spec(spec, view=self, request=request)
                    if isinstance(spec, PolymorphicServiceSpec)
                    else spec
                )
                instance: Any = resolve_mutation_instance(self, concrete) if detail else None
                # ``success_status`` is resolved per-request inside the dispatch
                # (``spec.success_status`` may be a callable keyed on the result),
                # so the decorator passes only the action default (200) — it is
                # *not* frozen here at decoration time.
                return dispatch_mutation_for_spec(
                    self,
                    request,
                    concrete,
                    instance=instance,
                    default_status=drf_status.HTTP_200_OK,
                    # Detail actions are update-shaped: a service that mutates in
                    # place and returns ``None`` renders the instance. Non-detail
                    # actions have no instance, so the flag is moot.
                    render_instance_on_none=detail,
                )
            except _AdditionalInputAPIException:
                _restore_schema_when_handled(self)
                raise

        # Stash the spec on the handler so schema generators (and any future
        # introspection) can recover it; the closure is otherwise opaque.
        handler._service_spec = spec  # ty: ignore[unresolved-attribute]
        return action(**drf_kwargs)(handler)

    return decorator
