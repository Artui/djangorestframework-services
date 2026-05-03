"""Strict ``CreateService`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from typing import Protocol, TypeVar

from typing_extensions import Unpack

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT")


class StrictCreateService(Protocol[InputT, ExtraT, ResultT]):
    """Strict shape for a create-action service.

    Identical to :class:`CreateService` except the ``**kwargs: Any`` escape
    hatch is replaced with ``**extras: Unpack[ExtraT]`` (:pep:`692`). When
    ``ExtraT`` is a ``TypedDict``, type checkers enforce that the service
    declares exactly the extra keys delivered by ``ServiceSpec.kwargs`` —
    nothing more, nothing less.

    ``request`` and ``user`` are deliberately *not* part of the fixed
    signature. The framework still places them in the kwargs pool, but
    services that want them must declare them on their ``ExtraT`` — most
    cleanly by subclassing
    :class:`~rest_framework_services.types.http_extras.HttpExtras`::

        class CreateAuthorKwargs(HttpExtras[MyUser]):
            tenant_id: int

        @implements(StrictCreateService[AuthorIn, CreateAuthorKwargs, Author])
        def create_author(
            *,
            data: AuthorIn,
            **extras: Unpack[CreateAuthorKwargs],
        ) -> Author: ...

    Services that don't read ``request`` / ``user`` should simply omit
    them — their ``ExtraT`` carries only the genuine extras (or
    :class:`~rest_framework_services.types.no_kwargs.NoKwargs` if there
    are none).
    """

    def __call__(
        self,
        *,
        data: InputT,
        **extras: Unpack[ExtraT],
    ) -> ResultT: ...
