"""``can_present_nothing``: django-service-specs' own, asked of what a spec declares."""

from __future__ import annotations

from django_service_specs.schema.can_present_nothing import (
    can_present_nothing as _can_present_nothing,
)
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from django_service_specs.specs.spec_source import SpecSource

from rest_framework_services.audience.utils import described_output


def can_present_nothing(spec: KernelServiceSpec | KernelSelectorSpec | SpecSource) -> bool:
    """Whether a successful dispatch of ``spec`` may present ``None`` as its result.

    django-service-specs'
    [`can_present_nothing`][django_service_specs.schema.can_present_nothing.can_present_nothing],
    asked of the compiled spec, so the output schema and every transport
    advertising one of its own give the same answer:

    - **A ``SelectorSpec``**: a ``RETRIEVE`` declaring ``allow_none``. A ``LIST``
      presents its rows, ``[]`` for none, and never ``None``.
    - **A ``ServiceSpec`` with an ``output_selector_spec``**: a ``RETRIEVE``
      re-read, whatever ``allow_none`` says, because a re-read finding nothing
      after the service has run is ``None`` rather than not-found. A ``LIST``
      re-read never presents ``None``.
    - **Any other ``ServiceSpec``** answers its own ``allow_none``.

    ``many`` is not read. A ``SelectorSpec`` with no ``selector`` is answered
    from its declaration, as its output schema is, since answering needs no
    selector to run.
    """
    return _can_present_nothing(described_output(spec))
