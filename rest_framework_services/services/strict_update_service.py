"""Strict ``UpdateService`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from typing import Protocol, TypeVar

from rest_framework.request import Request
from typing_extensions import Unpack

from rest_framework_services.services.utils import UserT

InputT = TypeVar("InputT")
InstanceT = TypeVar("InstanceT")
ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT", bound=dict[str, object])


class StrictUpdateService(Protocol[InputT, InstanceT, ResultT, ExtraT]):
    """Strict shape for an update-action service.

    See :class:`StrictCreateService` for rationale. Pin the extras delivered
    by ``ServiceSpec.kwargs`` via a ``TypedDict``::

        class UpdateAuthorKwargs(TypedDict):
            tenant_id: int

        def update_author(
            *,
            instance: Author,
            data: AuthorIn,
            request: HttpRequest,
            user: UserT,
            tenant_id: int,
        ) -> Author: ...

        _: StrictUpdateService[AuthorIn, Author, Author, UpdateAuthorKwargs] = update_author
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
