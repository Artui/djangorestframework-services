"""``ListSelector`` Protocol — typed shape for list-action selector callables."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, TypeVar, Unpack

from rest_framework_services.types._any_extras import _AnyExtras

ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT", default=_AnyExtras)


class ListSelector(Protocol[ResultT, ExtraT]):
    """Structural shape for a list-action selector callable.

    The framework calls this from ``get_queryset()``; the returned iterable
    flows through DRF's filter backends, pagination, and ``output_serializer``.

    See :class:`~rest_framework_services.services.CreateService` for the
    lenient-vs-strict parameterisation rationale. Lenient form:
    ``ListSelector[Author]`` — ``ExtraT`` defaults to an arbitrary-key
    ``TypedDict``. Strict form: ``ListSelector[Author, ListAuthorsKwargs]``
    — pin the extras (URL kwargs plus any ``SelectorSpec.kwargs`` returns).
    """

    def __call__(
        self,
        **extras: Unpack[ExtraT],
    ) -> Iterable[ResultT]: ...
