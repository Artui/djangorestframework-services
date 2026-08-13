"""``base_serializer_context`` — the DRF serializer context, on or off HTTP."""

from __future__ import annotations

from typing import Any


def base_serializer_context(*, view: Any, request: Any) -> dict[str, Any]:
    """Build the baseline serializer ``context`` a spec-driven render starts from.

    Over HTTP every serializer DRF builds carries
    ``get_serializer_context`` —
    ``{"request", "format", "view"}`` — so serializers routinely read
    ``self.context["request"]`` unguarded (``build_absolute_uri``,
    ``request.user``, a permission check in a ``SerializerMethodField``). A
    spec's ``input_serializer_context`` / ``output_serializer_context`` provider
    is *additive* config layered on top of that baseline, not a replacement for
    it, so off HTTP — where there is no DRF view to ask — the baseline has to be
    synthesized rather than skipped. Without it the same serializer that renders
    over HTTP raises ``KeyError: 'request'`` when the spec is dispatched from an
    MCP tool call, a Pydantic-AI toolset, or a management command.

    Two sources, in order:

    - ``view.get_serializer_context()`` when the view has it — a real DRF view (the HTTP
      bulk path renders through the same helper). It is the view's own documented
      extension point and may already be overridden, so it wins.
    - Otherwise DRF's shape, synthesized from the ``view`` / ``request`` the caller
      passed: the synthetic pair from
      [`build_offline_context`][rest_framework_services.dispatch.build_offline_context.build_offline_context]
      off HTTP, or ``None`` when the caller supplied neither. ``format`` is always
      ``None`` — content negotiation is an HTTP-only concern.

    The keys are always present, mirroring HTTP: a serializer reading
    ``self.context["request"]`` off HTTP sees the synthetic request (and
    ``None`` only when the caller passed no request at all), never a
    ``KeyError``. Absolute-URI fields additionally need real headers — pass the
    ambient ``http_request`` to ``build_offline_context`` when the transport has
    one, as the MCP server does.

    The spec's provider is merged *over* this by
    ``resolve_output_context`` /
    ``resolve_input_context``, so a
    provider keeps the final say on every key — including these three.
    """
    getter: Any = getattr(view, "get_serializer_context", None)
    if callable(getter):
        return dict(getter())
    return {"request": request, "format": None, "view": view}


__all__ = ["base_serializer_context"]
