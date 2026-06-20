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
    """

    value: Any
    kind: str
    status: int


__all__ = ["DispatchResult"]
