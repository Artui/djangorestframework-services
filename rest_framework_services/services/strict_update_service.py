"""Strict ``UpdateService`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from typing import Protocol, TypeVar

from typing_extensions import Unpack

InputT = TypeVar("InputT")
InstanceT = TypeVar("InstanceT")
ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT")


class StrictUpdateService(Protocol[InputT, InstanceT, ExtraT, ResultT]):
    """Strict shape for an update-action service.

    See :class:`StrictCreateService` for rationale and the ``request`` /
    ``user`` discussion. Pin the extras delivered by ``ServiceSpec.kwargs``
    via a ``TypedDict``::

        class UpdateAuthorKwargs(HttpExtras[MyUser]):
            tenant_id: int

        @implements(StrictUpdateService[AuthorIn, Author, UpdateAuthorKwargs, Author])
        def update_author(
            *,
            instance: Author,
            data: AuthorIn,
            **extras: Unpack[UpdateAuthorKwargs],
        ) -> Author: ...
    """

    def __call__(
        self,
        *,
        instance: InstanceT,
        data: InputT,
        **extras: Unpack[ExtraT],
    ) -> ResultT: ...
