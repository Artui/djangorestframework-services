"""``project_payload`` — shape a rendered payload for an agent audience."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.types.agent_projection import AgentProjection
from rest_framework_services.types.field_audience import FieldAudience


def project_payload(payload: Any, projection: AgentProjection) -> Any:
    """Drop plumbing and speak enum labels, at any depth.

    Two changes, both driven by the same declaration that shapes the schema so
    the two cannot disagree:

    - fields marked ``HIDDEN`` are **removed**. Removed rather than nested under
      some reserved subtree: a payload that is also emitted as text is read in
      full either way, so relocating a field costs its keys again and hides
      nothing. What an agent must never use should not be there.
    - a ``ChoiceField``'s constant is replaced by its display value, so the enum
      does not have to be spelled out to a person as ``PENDING_REVIEW``. **Not**
      on a ``HANDLE``, which some other tool takes as input.

    Apply this where a payload becomes the agent's *answer*. Not where it feeds
    the next step of a chain, which still needs the handles.
    """
    if projection.is_empty():
        return payload
    return _project(payload, projection)


def _project(payload: Any, projection: AgentProjection) -> Any:
    if isinstance(payload, list):
        return [_project(item, projection) for item in payload]
    if not isinstance(payload, Mapping):
        return payload
    projected: dict[str, Any] = {}
    for key, value in payload.items():
        audience = projection.audience(key)
        if audience is FieldAudience.HIDDEN:
            continue
        child = projection.nested.get(key)
        if child is not None:
            projected[key] = _project(value, child)
        elif audience is not FieldAudience.HANDLE and key in projection.choice_labels:
            projected[key] = projection.choice_labels[key].get(value, value)
        else:
            projected[key] = value
    return projected
