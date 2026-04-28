"""Strict ``CreateService`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from typing import Protocol, TypeVar

from rest_framework.request import Request
from typing_extensions import Unpack

from rest_framework_services.services.utils import UserT

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT", bound=dict[str, object])


class StrictCreateService(Protocol[InputT, ResultT, ExtraT]):
    """Strict shape for a create-action service.

    Identical to :class:`CreateService` except the ``**kwargs: Any`` escape
    hatch is replaced with ``**extras: Unpack[ExtraT]`` (:pep:`692`). When
    ``ExtraT`` is a ``TypedDict``, type checkers enforce that the service
    declares exactly the extra keys delivered by ``ServiceSpec.kwargs`` —
    nothing more, nothing less.

    Use it for services where you want drift-free signatures::

        class CreateAuthorKwargs(TypedDict):
            tenant_id: int

        def create_author(
            *,
            data: AuthorIn,
            request: HttpRequest,
            user: UserT,
            tenant_id: int,
        ) -> Author: ...

        # Static check that the function matches the strict Protocol:
        _: StrictCreateService[AuthorIn, Author, CreateAuthorKwargs] = create_author
    """

    def __call__(
        self,
        *,
        data: InputT,
        request: Request,
        user: UserT,
        **extras: Unpack[ExtraT],
    ) -> ResultT: ...
