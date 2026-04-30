"""Strict ``DeleteService`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from typing import Protocol, TypeVar

from rest_framework.request import Request
from typing_extensions import Unpack

from rest_framework_services.services.utils import UserT

InstanceT = TypeVar("InstanceT")
ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT")


class StrictDeleteService(Protocol[InstanceT, ExtraT, ResultT]):
    """Strict shape for a delete-action service.

    See :class:`StrictCreateService` for rationale. Most delete services
    return ``None`` — annotate ``ResultT`` as ``None`` and pair with
    :func:`~rest_framework_services.implements`::

        class DeleteAuthorKwargs(TypedDict):
            reason: str

        @implements(StrictDeleteService[Author, DeleteAuthorKwargs, None])
        def delete_author(
            *,
            instance: Author,
            request: HttpRequest,
            user: UserT,
            **extras: Unpack[DeleteAuthorKwargs],
        ) -> None: ...
    """

    def __call__(
        self,
        *,
        instance: InstanceT,
        request: Request,
        user: UserT,
        **extras: Unpack[ExtraT],
    ) -> ResultT: ...
