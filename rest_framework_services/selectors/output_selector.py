"""``OutputSelector`` Protocol — typed shape for mutation output selectors."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

InT = TypeVar("InT", contravariant=True)
OutT = TypeVar("OutT", covariant=True)


class OutputSelector(Protocol[InT, OutT]):
    """Structural shape for a ``ServiceSpec.output_selector`` callable.

    Invoked after the service returns. Receives the service's return value as
    ``result`` and may transform it (e.g. attach computed fields, swap to a
    different shape) before serialization.

    See :class:`~rest_framework_services.services.CreateService` for the
    extras-typing notes.
    """

    def __call__(
        self,
        *,
        result: InT,
        **extras: Any,
    ) -> OutT: ...
