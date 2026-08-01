"""``ViewHooks`` — a view's resolved hook layers, handed to the dispatch core."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ViewHooks:
    """The HTTP view's hook-chain contributions, resolved and passed down.

    ``dispatch_spec`` is the single execution core, but the view layer owns a
    configuration surface the core knows nothing about: the ``get_service_kwargs``
    / ``get_<action>_service_kwargs`` / ``get_input_data`` /
    ``get_<action>_input_data`` / ``get_*_serializer_context`` chains declared on
    :class:`MutationFlowMixin` and its viewset mixins. Those are genuinely
    HTTP-shaped — they are methods on a DRF view — so the core cannot resolve
    them, and a caller that resolves them has to be able to hand them over.
    This carrier is that hand-off.

    ⚠ **These are the *view* layers only — never the spec's own providers.**
    Each chain resolves ``view.get_<x>`` → ``view.get_<action>_<x>`` →
    ``spec.<x>``, spec winning on overlap. ``dispatch_spec`` owns that last
    layer, so a caller must pass ``spec_kwargs=None`` / ``spec_provider=None``
    when resolving these. Passing the *fully* resolved chain instead would make
    the core either skip its own spec resolution (a flag nobody would remember)
    or run the spec provider **twice** — and a ``spec.kwargs`` doing a tenant
    lookup is not idempotent. Keeping the split here is what lets precedence
    live in exactly one place.

    Four fields rather than one ``Mapping[str, Any]`` bag because they are
    consumed at four different points and one of them is not a mapping at all:

    - ``extra_kwargs`` merges into the dispatched callable's pool, beneath
      ``spec.kwargs``. Named for the chain it belongs to rather than for
      services, because the same carrier serves the selector chain
      (``get_selector_kwargs`` / ``get_<action>_selector_kwargs``) — only the
      view-method names differ, not the layering.
    - ``input_data`` merges onto the client payload *before* validation,
      beneath ``spec.input_data``; server-provided keys win over the client's.
    - ``input_serializer_context`` layers onto the baseline serializer context
      (:func:`base_serializer_context`), beneath ``spec.input_serializer_context``.
    - ``output_serializer_context`` is **lazy** — a callable taking the final
      post-selector ``result`` and returning the context — because the output
      context provider is documented to see the exact instance being serialized,
      which does not exist until after the service and output selector have run.

    Every field defaults to ``None`` (contributes nothing), so a transport with
    no view — MCP, an agent toolset, a management command — simply omits the
    argument and the core behaves exactly as it did before this existed.
    """

    extra_kwargs: Mapping[str, Any] | None = None
    input_data: Mapping[str, Any] | None = None
    input_serializer_context: Mapping[str, Any] | None = None
    output_serializer_context: Callable[[Any], Mapping[str, Any]] | None = None


__all__ = ["ViewHooks"]
