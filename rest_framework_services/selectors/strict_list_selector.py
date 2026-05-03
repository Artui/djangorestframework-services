"""Strict ``ListSelector`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, TypeVar

from typing_extensions import Unpack

ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT")


class StrictListSelector(Protocol[ExtraT, ResultT]):
    """Strict shape for a list-action selector.

    See :class:`~rest_framework_services.services.StrictCreateService` for
    rationale and the ``request`` / ``user`` discussion. ``ExtraT`` should
    declare both URL kwargs and any extras supplied by
    ``SelectorSpec.kwargs``::

        class ListAuthorsKwargs(HttpExtras[MyUser]):
            tenant_id: int

        @implements(StrictListSelector[ListAuthorsKwargs, Author])
        def list_authors(
            **extras: Unpack[ListAuthorsKwargs],
        ) -> Iterable[Author]: ...
    """

    def __call__(
        self,
        **extras: Unpack[ExtraT],
    ) -> Iterable[ResultT]: ...
