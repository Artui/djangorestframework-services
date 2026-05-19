"""``NoKwargs`` — empty ``TypedDict`` for strict services with no extras."""

from __future__ import annotations

from typing import TypedDict


class NoKwargs(TypedDict):
    """Empty :class:`TypedDict` for the ``ExtraT`` slot of strict service Protocols.

    Use this when a service has no per-spec ``ServiceSpec.kwargs`` and no
    ``get_*_service_kwargs`` override contributes anything beyond the
    framework-injected pool::

        @implements(CreateService[CreateAuthorIn, Author, NoKwargs])
        def create_author(
            *,
            data: CreateAuthorIn,
            **extras: Unpack[NoKwargs],
        ) -> Author: ...

    Saves every project from re-defining the same empty stub.
    """
