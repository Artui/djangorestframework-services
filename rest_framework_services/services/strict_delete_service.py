"""Strict ``DeleteService`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from typing import Protocol, TypeVar

from rest_framework.request import Request
from typing_extensions import Unpack

from rest_framework_services.services.utils import UserT

InstanceT = TypeVar("InstanceT")
ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT", bound=dict[str, object])


class StrictDeleteService(Protocol[InstanceT, ResultT, ExtraT]):
    """Strict shape for a delete-action service.

    See :class:`StrictCreateService` for rationale. Most delete services
    return ``None``; that's fine — annotate ``ResultT`` as ``None``.
    """

    def __call__(
        self,
        *,
        instance: InstanceT,
        request: Request,
        user: UserT,
        **extras: Unpack[ExtraT],
    ) -> ResultT: ...
