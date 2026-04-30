"""Strict ``UpdateService`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from typing import Protocol, TypeVar

from rest_framework.request import Request
from typing_extensions import Unpack

from rest_framework_services.services.utils import UserT

InputT = TypeVar("InputT")
InstanceT = TypeVar("InstanceT")
ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT")


class StrictUpdateService(Protocol[InputT, InstanceT, ExtraT, ResultT]):
    """Strict shape for an update-action service.

    See :class:`StrictCreateService` for rationale. Pin the extras delivered
    by ``ServiceSpec.kwargs`` via a ``TypedDict``::

        class UpdateAuthorKwargs(TypedDict):
            tenant_id: int

        @implements(StrictUpdateService[AuthorIn, Author, UpdateAuthorKwargs, Author])
        def update_author(
            *,
            instance: Author,
            data: AuthorIn,
            request: HttpRequest,
            user: UserT,
            **extras: Unpack[UpdateAuthorKwargs],
        ) -> Author: ...
    """

    def __call__(
        self,
        *,
        instance: InstanceT,
        data: InputT,
        request: Request,
        user: UserT,
        **extras: Unpack[ExtraT],
    ) -> ResultT: ...
