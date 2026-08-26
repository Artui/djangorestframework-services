"""``arender_for_agent`` — async sibling of
[`render_for_agent`][rest_framework_services.dispatch.render_for_agent.render_for_agent]."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.dispatch.render_for_agent import render_for_agent
from rest_framework_services.dispatch.utils import arun_off_loop
from rest_framework_services.types.agent_projection import AgentProjection
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.view_hooks import ViewHooks


async def arender_for_agent(
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
    """Async
    [`render_for_agent`][rest_framework_services.dispatch.render_for_agent.render_for_agent].

    Identical arguments, identical result. The whole render — and the projection
    that follows it — runs in Django's thread-sensitive executor, for the same
    reason
    [`arender_spec_output`][rest_framework_services.dispatch.arender_spec_output.arender_spec_output]
    exists: rendering evaluates querysets and traverses relations, so an async
    caller cannot do it inline without ``SynchronousOnlyOperation``.
    """
    return await arun_off_loop(
        render_for_agent,
        spec,
        value,
        projection=projection,
        many=many,
        view=view,
        request=request,
        extras=extras,
        view_hooks=view_hooks,
    )
