"""Lenient ``UpdateService`` Protocol — opt-in shape for update services."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from rest_framework.request import Request

from rest_framework_services.services.utils import UserT

InputT = TypeVar("InputT")
InstanceT = TypeVar("InstanceT")
ResultT = TypeVar("ResultT", covariant=True)


class UpdateService(Protocol[InputT, InstanceT, ResultT]):
    """Structural shape for an update-action service callable.

    Receives the resolved ``instance`` plus the validated ``data``. Returning
    ``None`` instructs the framework to render the in-memory ``instance``
    (mirroring DRF's ``UpdateAPIView`` shape).

    Lenient by design — see :class:`CreateService` for rationale.
    """

    def __call__(
        self,
        *,
        instance: InstanceT,
        data: InputT,
        request: Request,
        user: UserT,
        **kwargs: Any,
    ) -> ResultT: ...
