"""``project_payload`` — shape a rendered payload for an agent audience."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.types.audience_projection import AudienceProjection
from rest_framework_services.types.field_audience import FieldAudience


def project_payload(payload: Any, projection: AudienceProjection) -> Any:
    """Drop plumbing and speak enum labels, at any depth.

    Two changes, both driven by the same declaration that shapes the schema so
    the two cannot disagree:

    - fields marked ``HIDDEN`` are **removed**. Removed rather than nested under
      some reserved subtree: a payload that is also emitted as text is read in
      full either way, so relocating a field costs its keys again and hides
      nothing. What an agent must never use should not be there.
    - a ``ChoiceField``'s constant is replaced by its display value, so the enum
      does not have to be spelled out to a person as ``PENDING_REVIEW``. **Not**
      on a ``HANDLE``, which some other tool takes as input. A
      ``MultipleChoiceField`` renders a *collection* of constants, so each
      member is substituted rather than the collection looked up whole.
    - a field carrying a
      [`ValueFormatter`][rest_framework_services.types.value_formatter.ValueFormatter]
      is rendered through it — a date-time read as a local date-time rather than
      raw ISO-8601, an amount with its currency. Also **not** on a ``HANDLE``,
      and for the same reason.

    Apply this where a payload becomes the agent's *answer*. Not where it feeds
    the next step of a chain, which still needs the handles.
    """
    if projection.is_empty():
        return payload
    return _project(payload, projection)


def _project(payload: Any, projection: AudienceProjection) -> Any:
    if isinstance(payload, list):
        return [_project(item, projection) for item in payload]
    if not isinstance(payload, Mapping):
        return payload
    projected: dict[str, Any] = {}
    for key, value in payload.items():
        audience = projection.audience(key)
        if audience is FieldAudience.HIDDEN:
            continue
        # Declared beats derived, deliberately and not as a by-product of which
        # branch this chain happens to reach first. A ``ChoiceField`` its author
        # has also given a formatter is a real collision, and the transform
        # written by hand is the one that was asked for. ``annotate_output_schema``
        # orders its mirror of this chain the same way; the two agree only while
        # they are read together.
        formatter = projection.formatter(key)
        child = projection.nested.get(key)
        if formatter is not None:
            projected[key] = formatter.apply(value)
        elif child is not None:
            projected[key] = _project(value, child)
        elif audience is not FieldAudience.HANDLE and key in projection.choice_labels:
            projected[key] = _spoken(value, projection.choice_labels[key])
        else:
            projected[key] = value
    return projected


def _spoken(value: Any, labels: Mapping[Any, str]) -> Any:
    """The display value for a rendered choice, or the value unchanged.

    ``MultipleChoiceField`` subclasses ``ChoiceField`` and renders a *set* of
    constants, so a whole-value lookup would hash a list and raise. Only a
    ``ChoiceField`` reaches here, so those two shapes are the whole domain.

    An unrecognised constant passes through: a stale row naming a choice that has
    since been removed should still be reported, not crash the call.
    """
    if isinstance(value, list | tuple | set | frozenset):
        return [labels.get(member, member) for member in value]
    return labels.get(value, value)
