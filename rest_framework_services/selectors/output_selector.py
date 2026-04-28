"""Lenient ``OutputSelector`` Protocol — opt-in shape for mutation output selectors."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from rest_framework.request import Request

from rest_framework_services.services.utils import UserT

InT = TypeVar("InT")
OutT = TypeVar("OutT", covariant=True)


class OutputSelector(Protocol[InT, OutT]):
    """Structural shape for a ``ServiceSpec.output_selector`` callable.

    Invoked after the service returns. Receives the service's return value as
    ``result`` and may transform it (e.g. attach computed fields, swap to a
    different shape) before serialization.

    Lenient by design — see :class:`~rest_framework_services.services.CreateService`
    for rationale.
    """

    def __call__(
        self,
        *,
        result: InT,
        request: Request,
        user: UserT,
        **kwargs: Any,
    ) -> OutT: ...
