"""``CreateService`` Protocol — typed shape for create-action service callables."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

InputT = TypeVar("InputT", contravariant=True)
ResultT = TypeVar("ResultT", covariant=True)


class CreateService(Protocol[InputT, ResultT]):
    """Structural shape for a create-action service callable.

    ``data`` carries the validated input from the framework. ``**extras``
    absorbs whatever else the framework's kwargs pool delivers — ``request``,
    ``user``, URL kwargs, ``ServiceSpec.kwargs`` returns — without the service
    having to declare each key. The Protocol types ``**extras`` as ``Any`` so
    services on every major type checker (ty, mypy, pyright) conform.

    Strict-typed extras stay possible on your *own* function signature: declare
    your extras as a ``TypedDict`` with ``NotRequired`` keys and annotate
    ``**extras: Unpack[YourKw]``. Inside the function body, ``extras["foo"]``
    is then typed by ``YourKw``. The Protocol no longer carries a third type
    argument for the kwargs shape — that cross-check only ever worked under
    one minor version of one type checker (`ty` 0.0.32) and is not portable.
    """

    def __call__(
        self,
        *,
        data: InputT,
        **extras: Any,
    ) -> ResultT: ...
