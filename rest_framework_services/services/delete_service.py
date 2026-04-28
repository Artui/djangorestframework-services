"""Lenient ``DeleteService`` Protocol — opt-in shape for delete services."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from rest_framework.request import Request

from rest_framework_services.services.utils import UserT

InstanceT = TypeVar("InstanceT")
ResultT = TypeVar("ResultT", covariant=True)


class DeleteService(Protocol[InstanceT, ResultT]):
    """Structural shape for a delete-action service callable.

    Receives the resolved ``instance``. Most delete services return ``None``;
    if you need a response body, return a value and configure
    ``ServiceSpec.output_serializer`` (or ``output_selector``).

    Lenient by design — see :class:`CreateService` for rationale.
    """

    def __call__(
        self,
        *,
        instance: InstanceT,
        request: Request,
        user: UserT,
        **kwargs: Any,
    ) -> ResultT: ...
