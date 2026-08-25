"""``render_for_agent`` — render a dispatch result for an agent audience."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.audience.build_agent_projection import build_agent_projection
from rest_framework_services.audience.project_payload import project_payload
from rest_framework_services.dispatch.render_spec_output import render_spec_output
from rest_framework_services.dispatch.utils import output_serializer_for
from rest_framework_services.types.agent_projection import AgentProjection
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.view_hooks import ViewHooks


def render_for_agent(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    value: Any,
    *,
    projection: AgentProjection | None = None,
    many: bool = False,
    view: Any = None,
    request: Any = None,
    extras: Mapping[str, Any] | None = None,
    view_hooks: ViewHooks | None = None,
) -> Any:
    """[`render_spec_output`][rest_framework_services.dispatch.render_spec_output.render_spec_output]
    plus the agent projection.

    The single call an agent transport makes instead of ``render_spec_output``,
    so an MCP server, an in-process toolset, and anything added later shape
    payloads identically rather than each growing its own post-processor. Two
    copies of the render path have drifted in this stack before; this exists so
    a third does not.

    Every argument other than ``projection`` is passed straight through and means
    exactly what it means there, pagination included.

    ``projection`` is the serializer's resolved markings. Omit it and one is
    derived from the spec's output serializer, which costs a serializer
    instantiation per call — a transport that registers its tools up front should
    build it once with
    [`build_agent_projection`][rest_framework_services.audience.build_agent_projection.build_agent_projection]
    and pass it in.

    Render the agent's **answer** with this. A pipeline that feeds one spec's
    output into the next must keep rendering with ``render_spec_output``, or the
    handles the next step reads by will have been projected away.
    """
    payload: Any = render_spec_output(
        spec,
        value,
        many=many,
        view=view,
        request=request,
        extras=extras,
        view_hooks=view_hooks,
    )
    if projection is None:
        projection = build_agent_projection(output_serializer_for(spec))
    return project_payload(payload, projection)
