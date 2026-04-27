"""``@service_action`` decorator — DRF ``@action`` plus service plumbing."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from rest_framework import status as drf_status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from rest_framework_services.views.mutation.utils import _execute_mutation


def service_action(
    *,
    service: Callable[..., Any],
    detail: bool = False,
    methods: list[str] | None = None,
    input_serializer: type | None = None,
    output_serializer: type[Serializer] | None = None,
    output_selector: Callable[..., Any] | None = None,
    atomic: bool = True,
    success_status: int = drf_status.HTTP_200_OK,
    url_path: str | None = None,
    url_name: str | None = None,
    **action_kwargs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Wrap a viewset method as a service-backed custom action.

    The decorated method's body is *not* executed — the decorator supplies
    the handler. The method exists so that ``@service_action`` can attach
    DRF ``@action`` metadata and pick up the action name from ``__name__``.
    """
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
                service=service,
                input_serializer=input_serializer,
                output_serializer=output_serializer,
                output_selector=output_selector,
                atomic=atomic,
                success_status=success_status,
                instance=instance,
                extra_kwargs=extras,
            )

        return action(**drf_kwargs)(handler)

    return decorator
