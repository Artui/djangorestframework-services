"""``DispatchResult`` — the structured outcome of a transport-neutral dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DispatchResult:
    """What :func:`~rest_framework_services.dispatch_spec` resolved, pre-wire.

    A transport-neutral carrier the caller formats for its own wire (an HTTP
    ``Response``, an MCP ``ToolResult``, …). It holds the **raw** resolved
    domain value — never a paginated page or rendered serializer output —
    because ordering, pagination, and the response envelope are transport
    concerns. Render the value through
    :func:`~rest_framework_services.render_spec_output`.

    Fields:

    - **``value``** — the resolved value: a single instance (``RETRIEVE`` /
      mutation result), a queryset / iterable (``LIST``), or ``None`` (a
      nullable retrieve under ``allow_none``, a missing instance, or a
      side-effect-only mutation).
    - **``kind``** — one of ``"instance"`` (a single value, possibly ``None``),
      ``"list"`` (a collection to order / paginate / render ``many=True``), or
      ``"not_found"`` (a required instance could not be resolved).
    - **``status``** — an HTTP-ish status hint the transport may map to its
      wire: the spec's success status for mutations, ``200`` for reads, ``404``
      for ``"not_found"``.
    - **``service_result``** — the service's *own* return value, captured before
      an ``output_selector_spec`` re-fetch replaced it in ``value``. The two
      differ whenever an output selector runs, and the distinction is
      load-bearing: the service's return is the **flags carrier** (an upsert
      DTO's ``created``, a domain outcome enum), which is what a callable
      ``success_status`` and a ``response_finalizer`` key on, while ``value`` is
      the thing to render. ``None`` on the read path — a selector has no service.
    - **``data``** — the validated input (``serializer.validated_data``), or
      ``None`` when the spec declares no ``input_serializer``. Carried so a
      transport can key a post-dispatch decision on what was actually validated
      without re-running the serializer.

    The last two are informational: a transport that only renders reads
    ``value`` / ``kind`` / ``status`` and can ignore them.
    """

    value: Any
    kind: str
    status: int
    service_result: Any = None
    data: Any = None


__all__ = ["DispatchResult"]
