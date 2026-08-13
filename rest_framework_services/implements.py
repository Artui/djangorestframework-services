"""Identity decorator that asserts a callable matches a Protocol shape."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F")


def implements(proto: type[F]) -> Callable[[F], F]:
    """Identity decorator: assert ``fn`` structurally matches ``proto``.

    ``proto`` is a parameterised service or selector ``Protocol``::

        @implements(CreateService[AuthorIn, Author])
        def create_author(
            *,
            data: AuthorIn,
            **extras: Any,
        ) -> Author: ...

    Strict-typed extras stay on your function: declare a ``TypedDict`` with
    ``NotRequired`` keys (so the function still conforms to a Protocol whose caller may
    not supply those keys) and annotate ``**extras: Unpack[YourKw]``. The Protocol
    itself does not carry an extras-shape parameter — see
    [`CreateService`][rest_framework_services.services.create_service.CreateService] for
    the rationale.

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
