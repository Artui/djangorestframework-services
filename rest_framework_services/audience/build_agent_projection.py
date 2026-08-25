"""``build_agent_projection`` — read a serializer's agent markings."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers

from rest_framework_services.types.agent_field import AGENT, AgentField
from rest_framework_services.types.agent_projection import AgentProjection
from rest_framework_services.types.field_audience import FieldAudience


def build_agent_projection(serializer_cls: type | None) -> AgentProjection:
    """Resolve one serializer class's agent presentation, recursing into children.

    Reads
    [`AgentField`][rest_framework_services.types.agent_field.AgentField] markings
    out of each field's ``style`` bag and collects the ``ChoiceField`` labels DRF
    already holds. Pure in the serializer class, so the result is built once and
    reused — see
    [`AgentProjection`][rest_framework_services.types.agent_projection.AgentProjection].

    A class that is not a DRF serializer (a plain ``@dataclass`` output, or
    ``None``) yields an empty projection rather than an error: not every spec
    renders through a serializer, and nothing is marked up in those that don't.

    Raises:
        django.core.exceptions.ImproperlyConfigured: If a field carries
            something other than an ``AgentField`` under ``AGENT``, or if two
            fields both claim ``LABEL``. Neither can be caught later — the first
            would silently do nothing, and the second would silently pick one.
    """
    if not (
        isinstance(serializer_cls, type) and issubclass(serializer_cls, serializers.Serializer)
    ):
        return AgentProjection()
    # Genuine circular import, deliberately local: ``dispatch`` re-exports
    # ``render_for_agent``, which imports this module, so importing anything
    # from ``dispatch`` at module scope executes a half-built package.
    from rest_framework_services.dispatch.base_serializer_context import (
        base_serializer_context,
    )

    # The same baseline ``render_spec_output`` renders with. A serializer whose
    # ``get_fields`` reads ``self.context['request']`` -- routine, since over
    # HTTP the key is always there -- would otherwise raise ``KeyError`` here
    # and only here, breaking the documented swap to ``render_for_agent``.
    return _project(serializer_cls(context=base_serializer_context(view=None, request=None)))


def _project(serializer: serializers.Serializer) -> AgentProjection:
    marked: dict[str, AgentField] = {}
    choice_labels: dict[str, dict[Any, str]] = {}
    nested: dict[str, AgentProjection] = {}
    label: str | None = None
    for name, bound in serializer.fields.items():
        marking = _marking(bound, serializer=serializer, field_name=name)
        if marking is not None:
            marked[name] = marking
            if marking.audience is FieldAudience.LABEL:
                if label is not None:
                    raise ImproperlyConfigured(
                        f"{type(serializer).__name__}: both {label!r} and {name!r} are "
                        f"marked AgentField.label(). A record has one name — pick the "
                        f"field an agent should call it by."
                    )
                label = name
        if isinstance(bound, serializers.ChoiceField):
            labels = {v: str(d) for v, d in bound.choices.items() if str(d) != str(v)}
            if labels:
                choice_labels[name] = labels
        # ``ListSerializer`` (``many=True``) and ``ListField(child=Serializer())``
        # both put a serializer one level down, and the schema walk descends
        # into both. Missing either here fails *open* -- a hidden field inside
        # the child would survive in the payload.
        child = (
            bound.child
            if isinstance(bound, serializers.ListSerializer | serializers.ListField)
            else bound
        )
        if isinstance(child, serializers.Serializer):
            child_projection = _project(child)
            if not child_projection.is_empty():
                nested[name] = child_projection
    return AgentProjection(fields=marked, label=label, choice_labels=choice_labels, nested=nested)


def _marking(
    bound: serializers.Field, *, serializer: serializers.Serializer, field_name: str
) -> AgentField | None:
    """The field's ``AgentField``, or ``None``.

    Matches on the *value*, not on ``AGENT`` being present, so a marking filed
    under some other key still counts — the key is a naming courtesy, and an
    ``AgentField`` can only have come from the person who declared it. A
    non-``AgentField`` sitting under ``AGENT`` is the one case that raises: it is
    the shape a half-finished migration leaves behind (a bare ``"handle"``), and
    it would otherwise do nothing at all.
    """
    style: dict[Any, Any] = bound.style or {}
    if AGENT in style and not isinstance(style[AGENT], AgentField):
        raise ImproperlyConfigured(
            f"{type(serializer).__name__}.{field_name}: style[{AGENT!r}] is "
            f"{style[AGENT]!r}, not an AgentField. Use AgentField.handle() / "
            f".hidden() / .label() — a bare value here is silently ignored."
        )
    for value in style.values():
        if isinstance(value, AgentField):
            return value
    return None
