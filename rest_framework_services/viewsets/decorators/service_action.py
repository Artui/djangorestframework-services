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
from rest_framework_services.views.mutation.utils import _execute_mutation


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
    success_status: int = (
        spec.success_status if spec.success_status is not None else drf_status.HTTP_200_OK
    )

    drf_kwargs: dict[str, Any] = {"detail": detail}
    if methods is not None:
        drf_kwargs["methods"] = methods
    if url_path is not None:
        drf_kwargs["url_path"] = url_path
    if url_name is not None:
        drf_kwargs["url_name"] = url_name
    drf_kwargs.update(action_kwargs)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def handler(self: Any, request: Request, *args: Any, **kwargs: Any) -> Response:
            instance: Any = self.get_object() if detail else None
            extras: dict[str, Any] = {}
            get_extra: Callable[[], dict[str, Any]] | None = getattr(
                self, "get_service_kwargs", None
            )
            if get_extra is not None:
                extras = dict(get_extra())
            return _execute_mutation(
                self,
                request,
                service=spec.service,
                input_serializer=spec.input_serializer,
                output_serializer=spec.output_serializer,
                output_selector=spec.output_selector,
                atomic=spec.atomic,
                success_status=success_status,
                instance=instance,
                extra_kwargs=extras,
            )

        return action(**drf_kwargs)(handler)

    return decorator
