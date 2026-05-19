"""Identity decorator that asserts a callable matches a Protocol shape."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F")


def implements(proto: type[F]) -> Callable[[F], F]:
    """Identity decorator: assert ``fn`` structurally matches ``proto``.

    ``proto`` is a parameterized service or selector :class:`~typing.Protocol`,
    typically the strict (fully-parameterised) form::

        @implements(CreateService[AuthorIn, Author, CreateAuthorKwargs])
        def create_author(
            *,
            data: AuthorIn,
            **extras: Unpack[CreateAuthorKwargs],
        ) -> Author: ...

    Drift between the decorated function and ``proto`` is reported at the
    decorator line by ``ty``. mypy refuses ``type[Protocol]`` arguments (the
    ``type-abstract`` rule); mypy users either silence that with
    ``# type: ignore[type-abstract]`` or keep using the legacy
    ``_: CreateService[...] = create_author`` shim alongside the def.

    Returns the function unchanged at runtime.
    """

    def _identity(fn: F) -> F:
        return fn

    return _identity
