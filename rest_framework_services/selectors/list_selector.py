"""Lenient ``ListSelector`` Protocol — opt-in shape for list selectors."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, TypeVar

from rest_framework.request import Request

from rest_framework_services.services.utils import UserT

ResultT = TypeVar("ResultT", covariant=True)


class ListSelector(Protocol[ResultT]):
    """Structural shape for a list-action selector callable.

    The framework calls this from ``get_queryset()``; the returned iterable
    flows through DRF's filter backends, pagination, and ``output_serializer``.

    Lenient by design: ``**kwargs: Any`` lets the framework deliver URL kwargs
    and extras from ``SelectorSpec.kwargs`` / ``get_selector_kwargs`` without
    forcing the selector to declare them. Selectors may freely omit
    ``request`` / ``user`` (or any other pool key) they do not need; the
    framework inspects the signature and only passes what is declared.
    """

    def __call__(
        self,
        *,
        request: Request,
        user: UserT,
        **kwargs: Any,
    ) -> Iterable[ResultT]: ...
