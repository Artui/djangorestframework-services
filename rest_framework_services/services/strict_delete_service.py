"""Strict ``DeleteService`` Protocol — no ``**kwargs: Any`` escape hatch."""

from __future__ import annotations

from typing import Protocol, TypeVar

from typing_extensions import Unpack

InputT = TypeVar("InputT")
InstanceT = TypeVar("InstanceT")
ResultT = TypeVar("ResultT", covariant=True)
ExtraT = TypeVar("ExtraT")


class StrictDeleteService(Protocol[InputT, InstanceT, ExtraT, ResultT]):
    """Strict shape for a delete-action service.

    See :class:`StrictCreateService` for rationale and the ``request`` /
    ``user`` discussion. Most delete services return ``None`` — annotate
    ``ResultT`` as ``None`` and pair with
    :func:`~rest_framework_services.implements`.

    Type parameter ordering matches :class:`StrictCreateService` /
    :class:`StrictUpdateService`: ``InputT`` first.

    *Without* a request body — bind ``InputT`` to
    :class:`~rest_framework_services.types.no_input.NoInput` and rely on
    the Protocol-level :data:`Ellipsis` default for ``data``::

        class DeleteAuthorKwargs(HttpExtras[MyUser]):
            reason: str

        @implements(StrictDeleteService[NoInput, Author, NoKwargs, None])
        def delete_author(
            *,
            instance: Author,
            **extras: Unpack[NoKwargs],
        ) -> None: ...

    *With* a request body — bind ``InputT`` to your input dataclass and
    declare ``data`` on the service::

        @implements(StrictDeleteService[ReasonIn, Author, DeleteAuthorKwargs, None])
        def delete_author(
            *,
            instance: Author,
            data: ReasonIn,
            **extras: Unpack[DeleteAuthorKwargs],
        ) -> None: ...
    """

    def __call__(
        self,
        *,
        instance: InstanceT,
        data: InputT = ...,  # type: ignore[assignment]
        **extras: Unpack[ExtraT],
    ) -> ResultT: ...
