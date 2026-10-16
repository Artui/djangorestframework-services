"""``audience_projection_for_spec``: django-service-specs' own, asked of what a spec declares."""

from __future__ import annotations

from collections.abc import Mapping

from django_service_specs.dispatch.audience_projection_for_spec import (
    audience_projection_for_spec as _audience_projection_for_spec,
)
from django_service_specs.output.audience_projection import AudienceProjection
from django_service_specs.output.field_marking import FieldMarking
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from django_service_specs.specs.spec_source import SpecSource

from rest_framework_services.audience.utils import described_output


def audience_projection_for_spec(
    spec: KernelServiceSpec | KernelSelectorSpec | SpecSource,
    *,
    overrides: Mapping[str, FieldMarking] | None = None,
    name: str | None = None,
) -> AudienceProjection:
    """The ``AudienceProjection`` of what ``spec`` presents.

    django-service-specs'
    [`audience_projection_for_spec`][django_service_specs.dispatch.audience_projection_for_spec.audience_projection_for_spec]:
    the field markings of the compiled spec's output, with ``overrides`` laid
    over them, and ``name`` naming the projection in a refusal. A
    ``SelectorSpec`` with no ``selector`` is projected from its declaration,
    since projecting runs no selector.
    """
    return _audience_projection_for_spec(described_output(spec), overrides=overrides, name=name)
