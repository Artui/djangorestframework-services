"""``CreateService`` Protocol — typed shape for create-action service callables."""

from __future__ import annotations

from typing import Protocol, TypeVar, Unpack

from rest_framework_services.types._any_extras import _AnyExtras

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT", default=_AnyExtras)


class CreateService(Protocol[InputT, ResultT, ExtraT]):
    """Structural shape for a create-action service callable.

    Two parameterisations of the same shape:

    * ``CreateService[AuthorIn, Author]`` — lenient. ``ExtraT`` defaults to
      a ``TypedDict`` carrying arbitrary :class:`~typing.Any`-typed keys
      (via :pep:`728`'s ``extra_items=Any``), so the service can accept
      whatever the framework's kwargs pool delivers — ``request``, ``user``,
      URL kwargs, ``ServiceSpec.kwargs`` returns — without declaring them.
    * ``CreateService[AuthorIn, Author, MyExtras]`` — strict. Bind
      ``ExtraT`` to a ``TypedDict`` to pin the extras contract; type
      checkers then enforce that the service declares exactly those keys
      (no more, no less). Subclass
      :class:`~rest_framework_services.types.http_extras.HttpExtras` if
      you need ``request`` / ``user`` to come through extras.

    ``request`` and ``user`` are not part of the fixed signature in either
    form — they flow through ``**extras`` like every other framework-pool
    key. Services that don't read them simply omit them.
    """

    def __call__(
        self,
        *,
        data: InputT,
        **extras: Unpack[ExtraT],
    ) -> ResultT: ...
