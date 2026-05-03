"""Strict ``RetrieveSelector`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from typing import Protocol, TypeVar

from typing_extensions import Unpack

ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT")


class StrictRetrieveSelector(Protocol[ExtraT, ResultT]):
    """Strict shape for a retrieve-action selector.

    See :class:`~rest_framework_services.services.StrictCreateService` for
    rationale and the ``request`` / ``user`` discussion. ``ExtraT``
    typically contains the URL lookup field (``pk``, ``slug``) plus any
    extras from ``SelectorSpec.kwargs``::

        class RetrieveAuthorKwargs(HttpExtras[MyUser]):
            pk: int
            tenant_id: int

        @implements(StrictRetrieveSelector[RetrieveAuthorKwargs, Author])
        def retrieve_author(
            **extras: Unpack[RetrieveAuthorKwargs],
        ) -> Author | None: ...
    """

    def __call__(
        self,
        **extras: Unpack[ExtraT],
    ) -> ResultT | None: ...
