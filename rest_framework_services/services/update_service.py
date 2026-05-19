"""``UpdateService`` Protocol — typed shape for update-action service callables."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

InputT = TypeVar("InputT", contravariant=True)
InstanceT = TypeVar("InstanceT", contravariant=True)
ResultT = TypeVar("ResultT", covariant=True)


class UpdateService(Protocol[InputT, InstanceT, ResultT]):
    """Structural shape for an update-action service callable.

    Receives the resolved ``instance`` plus the validated ``data``. Returning
    ``None`` instructs the framework to render the in-memory ``instance``
    (mirroring DRF's ``UpdateAPIView`` shape).

    See :class:`CreateService` for the extras-typing notes.
    """

    def __call__(
        self,
        *,
        instance: InstanceT,
        data: InputT,
        **extras: Any,
    ) -> ResultT: ...
