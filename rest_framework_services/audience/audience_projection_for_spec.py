"""``audience_projection_for_spec`` — a spec's field markings, resolved once."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.audience.build_audience_projection import build_audience_projection
from rest_framework_services.types.audience_projection import AudienceProjection
from rest_framework_services.types.field_marking import FieldMarking
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


def audience_projection_for_spec(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    *,
    overrides: Mapping[str, FieldMarking] | None = None,
    name: str | None = None,
) -> AudienceProjection:
    """Resolve the field markings on whatever serializer ``spec`` renders through.

    A selector keeps it on ``output_serializer``; a service keeps it one level
    down, on ``output_selector_spec``. A transport that registers its tools up
    front calls this once per spec and hands the result to
    [`render_for_audience`][rest_framework_services.dispatch.render_for_audience.render_for_audience],
    rather than paying a serializer instantiation on every call — and rather than
    each transport re-deriving where a spec keeps its output serializer.

    ``overrides`` and ``name`` are
    [`build_audience_projection`][rest_framework_services.audience.build_audience_projection.build_audience_projection]'s,
    forwarded: a mount holding an
    [`OfflineContract`][rest_framework_services.types.offline_contract.OfflineContract]
    passes its ``field_audiences`` straight through, and every agent transport
    layers that one declaration by the same rule.
    """
    # Genuine circular import, deliberately local: ``dispatch`` re-exports
    # ``render_for_audience``, which imports this package, so importing anything
    # from ``dispatch`` at module scope executes a half-built package.
    from rest_framework_services.dispatch.utils import output_serializer_for

    return build_audience_projection(output_serializer_for(spec), overrides=overrides, name=name)
