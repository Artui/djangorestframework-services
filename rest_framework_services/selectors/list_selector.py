"""``ListSelector`` Protocol — typed shape for list-action selector callables."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, TypeVar

ResultT = TypeVar("ResultT", covariant=True)


class ListSelector(Protocol[ResultT]):
    """Structural shape for a list-action selector callable.

    The framework calls this from ``get_queryset()``; the returned iterable
    flows through DRF's filter backends, pagination, and ``output_serializer``.

    See :class:`~rest_framework_services.services.CreateService` for the
    extras-typing notes.
    """

    def __call__(
        self,
        **extras: Any,
    ) -> Iterable[ResultT]: ...
