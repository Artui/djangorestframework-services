"""``@service_action`` decorator — DRF ``@action`` plus service plumbing."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from rest_framework import status as drf_status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.utils import (
    dispatch_mutation_for_spec,
    resolve_mutation_instance,
)
from rest_framework_services.views.spec_validation import validate_service_spec


def service_action(
    spec: ServiceSpec,
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

    Pass a :class:`ServiceSpec` for the service wiring. ``detail``,
    ``methods``, ``url_path``, ``url_name``, and any extra ``**action_kwargs``
    are forwarded to DRF's ``@action``.
    """
    drf_kwargs: dict[str, Any] = {"detail": detail}
    if methods is not None:
        drf_kwargs["methods"] = methods
    if url_path is not None:
        drf_kwargs["url_path"] = url_path
    if url_name is not None:
        drf_kwargs["url_name"] = url_name
    if spec.permission_classes is not None:
        drf_kwargs["permission_classes"] = list(spec.permission_classes)
    drf_kwargs.update(action_kwargs)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn_label = getattr(fn, "__qualname__", repr(fn))
        # The viewset class is unknown at decoration time, so any
        # ``get_<action>_service_kwargs`` / ``get_service_kwargs`` it may
        # carry has to be assumed permissive.
        validate_service_spec(
            spec,
            label=f"@service_action {fn_label}",
            has_instance=detail,
            permissive_extras=True,
        )

        @functools.wraps(fn)
        def handler(self: Any, request: Request, *args: Any, **kwargs: Any) -> Response:
            instance: Any = resolve_mutation_instance(self, spec) if detail else None
            # ``success_status`` is resolved per-request inside the dispatch
            # (``spec.success_status`` may be a callable keyed on the result),
            # so the decorator passes only the action default (200) — it is
            # *not* frozen here at decoration time.
            return dispatch_mutation_for_spec(
                self,
                request,
                spec,
                instance=instance,
                default_status=drf_status.HTTP_200_OK,
                # Detail actions are update-shaped: a service that mutates in
                # place and returns ``None`` renders the instance. Non-detail
                # actions have no instance, so the flag is moot.
                render_instance_on_none=detail,
            )

        # Stash the spec on the handler so schema generators (and any future
        # introspection) can recover it; the closure is otherwise opaque.
        handler._service_spec = spec  # ty: ignore[unresolved-attribute]
        return action(**drf_kwargs)(handler)

    return decorator
