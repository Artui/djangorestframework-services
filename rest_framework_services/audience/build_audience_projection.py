"""``build_audience_projection`` — read a serializer's field markings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers

from rest_framework_services.types.audience_projection import AudienceProjection
from rest_framework_services.types.field_audience import FieldAudience
from rest_framework_services.types.field_marking import MARKING, FieldMarking


def build_audience_projection(
    serializer_cls: type | None,
    *,
    overrides: Mapping[str, FieldMarking] | None = None,
    name: str | None = None,
) -> AudienceProjection:
    """Resolve one serializer class's agent presentation, recursing into children.

    Reads
    [`FieldMarking`][rest_framework_services.types.field_marking.FieldMarking] markings
    out of each field's ``style`` bag and collects the ``ChoiceField`` labels DRF
    already holds. Pure in the serializer class, so the result is built once and
    reused — see
    [`AudienceProjection`][rest_framework_services.types.audience_projection.AudienceProjection].

    A class that is not a DRF serializer (a plain ``@dataclass`` output, or
    ``None``) yields an empty projection rather than an error: not every spec
    renders through a serializer, and nothing is marked up in those that don't.

    ``overrides`` layers a caller's markings on top, for the one case the
    serializer cannot express: a mount that needs what its sibling hides. They
    reach a transport as
    [`OfflineContract.field_audiences`][rest_framework_services.types.offline_contract.OfflineContract],
    which is one declaration read by every agent transport — and the merge lives
    here, next to the clash rule it extends, so that stays true. Two copies of it
    is how one spec comes to project a different field set depending on which
    transport served it. ``name`` identifies the mount in the error message and
    defaults to the serializer's own class name.

    Raises:
        django.core.exceptions.ImproperlyConfigured: If a field carries
            something other than an ``FieldMarking`` under ``MARKING``, or if two
            fields both claim ``LABEL`` — whether the serializer declared the
            clash or an override introduced it. Neither can be caught later: the
            first would silently do nothing, and the second would silently pick
            one.
    """
    if isinstance(serializer_cls, type) and issubclass(serializer_cls, serializers.Serializer):
        # Genuine circular import, deliberately local: ``dispatch`` re-exports
        # ``render_for_audience``, which imports this module, so importing anything
        # from ``dispatch`` at module scope executes a half-built package.
        from rest_framework_services.dispatch.base_serializer_context import (
            base_serializer_context,
        )

        # The same baseline ``render_spec_output`` renders with. A serializer
        # whose ``get_fields`` reads ``self.context['request']`` -- routine,
        # since over HTTP the key is always there -- would otherwise raise
        # ``KeyError`` here and only here, breaking the documented swap to
        # ``render_for_audience``.
        projection = _project(
            serializer_cls(context=base_serializer_context(view=None, request=None))
        )
    else:
        projection = AudienceProjection()
    if not overrides:
        return projection
    return _with_overrides(
        projection,
        overrides,
        name=name or getattr(serializer_cls, "__name__", None) or "agent projection",
    )


def _with_overrides(
    projection: AudienceProjection, overrides: Mapping[str, FieldMarking], *, name: str
) -> AudienceProjection:
    """The serializer's markings with one mount's overrides layered over them.

    The serializer stays authoritative — it is the declaration every transport
    reads, and an override is the exception rather than a second place to declare
    an audience.

    Only ``fields`` and ``label`` can move. ``choice_labels`` come from the
    ``ChoiceField`` definitions and ``nested`` from child serializers; an
    override names neither, so both pass through.
    """
    fields: dict[str, FieldMarking] = {**projection.fields, **overrides}
    labels = [n for n, marking in fields.items() if marking.audience is FieldAudience.LABEL]
    if len(labels) > 1:
        raise ImproperlyConfigured(
            f"{name}: field_audiences leaves {labels!r} all marked as the label. "
            f"A record has one name — override the others to something else."
        )
    return AudienceProjection(
        fields=fields,
        label=labels[0] if labels else None,
        choice_labels=projection.choice_labels,
        nested=projection.nested,
    )


def _project(serializer: serializers.Serializer) -> AudienceProjection:
    marked: dict[str, FieldMarking] = {}
    choice_labels: dict[str, dict[Any, str]] = {}
    nested: dict[str, AudienceProjection] = {}
    label: str | None = None
    for name, bound in serializer.fields.items():
        marking = _marking(bound, serializer=serializer, field_name=name)
        if marking is not None:
            marked[name] = marking
            if marking.audience is FieldAudience.LABEL:
                if label is not None:
                    raise ImproperlyConfigured(
                        f"{type(serializer).__name__}: both {label!r} and {name!r} are "
                        f"marked FieldMarking.label(). A record has one name — pick the "
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
    return AudienceProjection(
        fields=marked, label=label, choice_labels=choice_labels, nested=nested
    )


def _marking(
    bound: serializers.Field, *, serializer: serializers.Serializer, field_name: str
) -> FieldMarking | None:
    """The field's ``FieldMarking``, or ``None``.

    Matches on the *value*, not on ``MARKING`` being present, so a marking filed
    under some other key still counts — the key is a naming courtesy, and an
    ``FieldMarking`` can only have come from the person who declared it. A
    non-``FieldMarking`` sitting under ``MARKING`` is the one case that raises: it is
    the shape a half-finished migration leaves behind (a bare ``"handle"``), and
    it would otherwise do nothing at all.
    """
    style: dict[Any, Any] = bound.style or {}
    if MARKING in style and not isinstance(style[MARKING], FieldMarking):
        raise ImproperlyConfigured(
            f"{type(serializer).__name__}.{field_name}: style[{MARKING!r}] is "
            f"{style[MARKING]!r}, not an FieldMarking. Use FieldMarking.handle() / "
            f".hidden() / .label() — a bare value here is silently ignored."
        )
    for value in style.values():
        if isinstance(value, FieldMarking):
            return value
    return None
