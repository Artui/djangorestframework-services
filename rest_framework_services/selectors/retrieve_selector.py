"""Lenient ``RetrieveSelector`` Protocol — opt-in shape for retrieve selectors."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from rest_framework.request import Request

from rest_framework_services.services.utils import UserT

ResultT = TypeVar("ResultT", covariant=True)


class RetrieveSelector(Protocol[ResultT]):
    """Structural shape for a retrieve-action selector callable.

    The framework calls this from ``get_object()``. Returning ``None`` (or
    raising ``Model.DoesNotExist``) results in a 404. The URL lookup field
    (typically ``pk``) is delivered via ``**kwargs``.

    Lenient by design — see :class:`ListSelector` for rationale.
    """

    def __call__(
        self,
        *,
        request: Request,
        user: UserT,
        **kwargs: Any,
    ) -> ResultT | None: ...
