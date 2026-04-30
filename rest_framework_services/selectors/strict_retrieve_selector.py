"""Strict ``RetrieveSelector`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from typing import Protocol, TypeVar

from rest_framework.request import Request
from typing_extensions import Unpack

from rest_framework_services.services.utils import UserT

ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT")


class StrictRetrieveSelector(Protocol[ExtraT, ResultT]):
    """Strict shape for a retrieve-action selector.

    See :class:`~rest_framework_services.services.StrictCreateService` for
    rationale. ``ExtraT`` typically contains the URL lookup field
    (``pk``, ``slug``) plus any extras from ``SelectorSpec.kwargs``::

        class RetrieveAuthorKwargs(TypedDict):
            pk: int
            tenant_id: int

        @implements(StrictRetrieveSelector[RetrieveAuthorKwargs, Author])
        def retrieve_author(
            *,
            request: HttpRequest,
            user: UserT,
            **extras: Unpack[RetrieveAuthorKwargs],
        ) -> Author | None: ...
    """

    def __call__(
        self,
        *,
        request: Request,
        user: UserT,
        **extras: Unpack[ExtraT],
    ) -> ResultT | None: ...
