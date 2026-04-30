"""Strict ``OutputSelector`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from typing import Protocol, TypeVar

from rest_framework.request import Request
from typing_extensions import Unpack

from rest_framework_services.services.utils import UserT

InT = TypeVar("InT")
OutT = TypeVar("OutT", covariant=True)
ExtraT = TypeVar("ExtraT")


class StrictOutputSelector(Protocol[InT, ExtraT, OutT]):
    """Strict shape for a ``ServiceSpec.output_selector``.

    See :class:`~rest_framework_services.services.StrictCreateService` for
    rationale. ``ExtraT`` declares the action-pool keys and any
    ``SelectorSpec.kwargs`` extras the output selector consumes::

        class RenderAuthorKwargs(TypedDict):
            rendered_at: str

        @implements(StrictOutputSelector[Author, RenderAuthorKwargs, AuthorOut])
        def render_author(
            *,
            result: Author,
            request: HttpRequest,
            user: UserT,
            **extras: Unpack[RenderAuthorKwargs],
        ) -> AuthorOut: ...
    """

    def __call__(
        self,
        *,
        result: InT,
        request: Request,
        user: UserT,
        **extras: Unpack[ExtraT],
    ) -> OutT: ...
