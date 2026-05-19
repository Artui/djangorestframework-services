"""``UpdateService`` Protocol — typed shape for update-action service callables."""

from __future__ import annotations

from typing import Protocol, TypeVar, Unpack

from rest_framework_services.types._any_extras import _AnyExtras

InputT = TypeVar("InputT")
InstanceT = TypeVar("InstanceT")
ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT", default=_AnyExtras)


class UpdateService(Protocol[InputT, InstanceT, ResultT, ExtraT]):
    """Structural shape for an update-action service callable.

    Receives the resolved ``instance`` plus the validated ``data``. Returning
    ``None`` instructs the framework to render the in-memory ``instance``
    (mirroring DRF's ``UpdateAPIView`` shape).

    See :class:`CreateService` for the lenient-vs-strict parameterisation
    rationale. Two example signatures::

        # Lenient — accepts arbitrary framework-pool keys
        UpdateService[AuthorIn, Author, Author]

        # Strict — pin extras via ``HttpExtras`` subclass
        class UpdateAuthorKwargs(HttpExtras[MyUser]):
            tenant_id: int

        UpdateService[AuthorIn, Author, Author, UpdateAuthorKwargs]
    """

    def __call__(
        self,
        *,
        instance: InstanceT,
        data: InputT,
        **extras: Unpack[ExtraT],
    ) -> ResultT: ...
