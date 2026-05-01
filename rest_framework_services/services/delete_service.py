"""Lenient ``DeleteService`` Protocol — opt-in shape for delete services."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from rest_framework.request import Request

from rest_framework_services.services.utils import UserT

InputT = TypeVar("InputT")
InstanceT = TypeVar("InstanceT")
ResultT = TypeVar("ResultT", covariant=True)


class DeleteService(Protocol[InputT, InstanceT, ResultT]):
    """Structural shape for a delete-action service callable.

    Receives the resolved ``instance``. Most delete services return ``None``;
    if you need a response body, return a value and configure
    ``ServiceSpec.output_serializer`` (or ``output_selector``).

    For *delete with payload* — when the spec has an ``input_serializer`` —
    bind ``InputT`` to your input dataclass and declare ``data`` on the
    service. ``data`` is optional in the Protocol (default :data:`Ellipsis`)
    so services that don't read a body can still match the shape by binding
    ``InputT`` to :class:`~rest_framework_services.types.no_input.NoInput`::

        @implements(DeleteService[ReasonIn, Author, None])
        def delete_author(
            *,
            instance: Author,
            data: ReasonIn,
            request: HttpRequest,
            user: UserT,
            **kwargs: Any,
        ) -> None: ...

    Lenient by design — see :class:`CreateService` for rationale.
    """

    def __call__(
        self,
        *,
        instance: InstanceT,
        data: InputT = ...,  # type: ignore[assignment]
        request: Request,
        user: UserT,
        **kwargs: Any,
    ) -> ResultT: ...
