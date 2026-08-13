"""``ViewHooks`` — a view's resolved hook layers, handed to the dispatch core."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ViewHooks:
    """The HTTP view's hook-chain contributions, resolved and passed down.

    ``dispatch_spec`` is the single execution core, but the view layer owns a
    configuration surface the core knows nothing about: the ``get_service_kwargs`` /
    ``get_<action>_service_kwargs`` / ``get_input_data`` / ``get_<action>_input_data`` /
    ``get_*_serializer_context`` chains declared on
    [`MutationFlowMixin`][rest_framework_services.views.mutation.mutation_flow_mixin.MutationFlowMixin]
    and its viewset mixins. Those are methods on a DRF view, so the core cannot resolve
    them; this carrier is how a caller that has resolved them hands them over.

    **These are the *view* layers only — never the spec's own providers.**
    Each chain resolves ``view.get_<x>`` → ``view.get_<action>_<x>`` →
    ``spec.<x>``, spec winning on overlap. ``dispatch_spec`` owns that last
    layer, so a caller must pass ``spec_kwargs=None`` / ``spec_provider=None``
    when resolving these; hand over the *fully* resolved chain instead and the
    core runs the spec provider **twice**, which a ``spec.kwargs`` doing a
    tenant lookup will not survive.

    Every field defaults to ``None`` (contributes nothing), so a transport with
    no view — MCP, an agent toolset, a management command — simply omits the
    argument and the core behaves exactly as it did before this existed.

    Attributes:
        extra_kwargs: Merges into the dispatched callable's pool, beneath
            ``spec.kwargs``. The same carrier serves the selector chain
            (``get_selector_kwargs`` / ``get_<action>_selector_kwargs``) — only
            the view-method names differ, not the layering.
        input_data: Merges onto the client payload *before* validation, beneath
            ``spec.input_data``; server-provided keys win over the client's.
        input_serializer_context: Layers onto the baseline serializer context
            ([`base_serializer_context`][rest_framework_services.dispatch.base_serializer_context.base_serializer_context]), beneath
            ``spec.input_serializer_context``.
        output_serializer_context: **Lazy** — a callable taking the final
            post-selector ``result`` and returning the context — because the
            output context provider is documented to see the exact instance
            being serialized, which does not exist until after the service and
            output selector have run.
        progress: The view's own progress sink, resolved for both chains — a
            selector can be long too (a large export is a selector). Reach for it
            only when a buffered request genuinely needs it: if a request runs
            long enough to want progress, a task plus polling is usually the right
            shape, and this seam is for the cases where that does not apply (a
            streaming response, or a websocket sidecar the host already runs).
    """

    # Naming: named for the chain it belongs to rather than for services,
    # because the same carrier serves the selector chain.
    extra_kwargs: Mapping[str, Any] | None = None
    input_data: Mapping[str, Any] | None = None
    input_serializer_context: Mapping[str, Any] | None = None
    output_serializer_context: Callable[[Any], Mapping[str, Any]] | None = None
    # The *transport-native* half of the progress fan-in: from the core's
    # perspective an HTTP view is simply the transport.
    progress: Any | None = None


__all__ = ["ViewHooks"]
