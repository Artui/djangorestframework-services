"""``RetrieveSelector`` Protocol — typed shape for retrieve-action selectors."""

from __future__ import annotations

from typing import Protocol, TypeVar, Unpack

from rest_framework_services.types._any_extras import _AnyExtras

ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT", default=_AnyExtras)


class RetrieveSelector(Protocol[ResultT, ExtraT]):
    """Structural shape for a retrieve-action selector callable.

    The framework calls this from ``get_object()``. Returning ``None`` (or
    raising ``Model.DoesNotExist``) results in a 404. The URL lookup field
    (typically ``pk``) is delivered via ``**extras``.

    See :class:`~rest_framework_services.services.CreateService` for the
    lenient-vs-strict parameterisation rationale.
    """

    def __call__(
        self,
        **extras: Unpack[ExtraT],
    ) -> ResultT | None: ...
