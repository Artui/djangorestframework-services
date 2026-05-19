"""``OutputSelector`` Protocol — typed shape for mutation output selectors."""

from __future__ import annotations

from typing import Protocol, TypeVar, Unpack

from rest_framework_services.types._any_extras import _AnyExtras

InT = TypeVar("InT")
OutT = TypeVar("OutT", covariant=True)
ExtraT = TypeVar("ExtraT", default=_AnyExtras)


class OutputSelector(Protocol[InT, OutT, ExtraT]):
    """Structural shape for a ``ServiceSpec.output_selector`` callable.

    Invoked after the service returns. Receives the service's return value as
    ``result`` and may transform it (e.g. attach computed fields, swap to a
    different shape) before serialization.

    See :class:`~rest_framework_services.services.CreateService` for the
    lenient-vs-strict parameterisation rationale.
    """

    def __call__(
        self,
        *,
        result: InT,
        **extras: Unpack[ExtraT],
    ) -> OutT: ...
