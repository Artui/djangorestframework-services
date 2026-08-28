"""``agent_projection_for_spec`` — a spec's agent markings, resolved once."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.audience.build_agent_projection import build_agent_projection
from rest_framework_services.types.agent_field import AgentField
from rest_framework_services.types.agent_projection import AgentProjection
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


def agent_projection_for_spec(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    *,
    overrides: Mapping[str, AgentField] | None = None,
    name: str | None = None,
) -> AgentProjection:
    """Resolve the agent markings on whatever serializer ``spec`` renders through.

    A selector keeps it on ``output_serializer``; a service keeps it one level
    down, on ``output_selector_spec``. A transport that registers its tools up
    front calls this once per spec and hands the result to
    [`render_for_agent`][rest_framework_services.dispatch.render_for_agent.render_for_agent],
    rather than paying a serializer instantiation on every call — and rather than
    each transport re-deriving where a spec keeps its output serializer.

    ``overrides`` and ``name`` are
    [`build_agent_projection`][rest_framework_services.audience.build_agent_projection.build_agent_projection]'s,
    forwarded: a mount holding an
    [`AgentContract`][rest_framework_services.types.agent_contract.AgentContract]
    passes its ``field_audiences`` straight through, and every agent transport
    layers that one declaration by the same rule.
    """
    # Genuine circular import, deliberately local: ``dispatch`` re-exports
    # ``render_for_agent``, which imports this package, so importing anything
    # from ``dispatch`` at module scope executes a half-built package.
    from rest_framework_services.dispatch.utils import output_serializer_for

    return build_agent_projection(output_serializer_for(spec), overrides=overrides, name=name)
