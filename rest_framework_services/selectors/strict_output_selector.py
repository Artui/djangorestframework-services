"""Strict ``OutputSelector`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from typing import Protocol, TypeVar

from rest_framework.request import Request
from typing_extensions import Unpack

from rest_framework_services.services.utils import UserT

InT = TypeVar("InT")
OutT = TypeVar("OutT", covariant=True)
ExtraT = TypeVar("ExtraT", bound=dict[str, object])


class StrictOutputSelector(Protocol[InT, OutT, ExtraT]):
    """Strict shape for a ``ServiceSpec.output_selector``.

    See :class:`~rest_framework_services.services.StrictCreateService` for
    rationale.
    """

    def __call__(
        self,
        *,
        result: InT,
        request: Request,
        user: UserT,
        **extras: Unpack[ExtraT],
    ) -> OutT: ...
