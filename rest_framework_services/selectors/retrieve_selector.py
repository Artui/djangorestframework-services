"""``RetrieveSelector`` Protocol — typed shape for retrieve-action selectors."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

ResultT = TypeVar("ResultT", covariant=True)


class RetrieveSelector(Protocol[ResultT]):
    """Structural shape for a retrieve-action selector callable.

    The framework calls this from ``get_object()``. Returning ``None`` (or
    raising ``Model.DoesNotExist``) results in a 404. The URL lookup field
    (typically ``pk``) is delivered via ``**extras``.

    See [`CreateService`][rest_framework_services.services.create_service.CreateService]
    for the extras-typing notes."""

    def __call__(
        self,
        **extras: Any,
    ) -> ResultT | None: ...
