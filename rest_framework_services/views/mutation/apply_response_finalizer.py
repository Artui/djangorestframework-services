"""``apply_response_finalizer`` — run a spec's post-serialization HTTP hook."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.views.utils import resolve_callable_kwargs


def apply_response_finalizer(
    finalizer: Callable[..., Response | None] | None,
    response: Response,
    *,
    request: Request,
    view: Any,
    result: Any,
    instance: Any = None,
    data: Any = None,
) -> Response:
    """Apply ``ServiceSpec.response_finalizer`` to a freshly built 2xx response.

    ``None`` (no finalizer) returns ``response`` unchanged. Otherwise the hook
    is resolved through the framework keyword pool — declaring any subset of
    ``response`` / ``result`` / ``request`` / ``view`` / ``instance`` / ``data``
    (or ``**kwargs``) — and its return value replaces the response when it is a
    ``Response``, or keeps the original when it returns ``None``.

    The pool mirrors the response-phase seam: unlike the service/selector pool
    it deliberately carries ``view`` (a response decision may read view/request
    context). ``instance`` / ``data`` are offered only when present (absent on
    create and on the bulk path), matching how the service pool gates them.
    """
    if finalizer is None:
        return response
    pool: dict[str, Any] = {
        "response": response,
        "request": request,
        "view": view,
        "result": result,
    }
    if instance is not None:
        pool["instance"] = instance
    if data is not None:
        pool["data"] = data
    finalized = finalizer(**resolve_callable_kwargs(finalizer, pool))
    return finalized if finalized is not None else response
