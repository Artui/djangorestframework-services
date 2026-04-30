"""Strict ``ListSelector`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, TypeVar

from rest_framework.request import Request
from typing_extensions import Unpack

from rest_framework_services.services.utils import UserT

ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT")


class StrictListSelector(Protocol[ExtraT, ResultT]):
    """Strict shape for a list-action selector.

    See :class:`~rest_framework_services.services.StrictCreateService` for
    rationale. ``ExtraT`` should declare both URL kwargs and any extras
    supplied by ``SelectorSpec.kwargs``::

        class ListAuthorsKwargs(TypedDict):
            tenant_id: int

        @implements(StrictListSelector[ListAuthorsKwargs, Author])
        def list_authors(
            *,
            request: HttpRequest,
            user: UserT,
            **extras: Unpack[ListAuthorsKwargs],
        ) -> Iterable[Author]: ...
    """

    def __call__(
        self,
        *,
        request: Request,
        user: UserT,
        **extras: Unpack[ExtraT],
    ) -> Iterable[ResultT]: ...
