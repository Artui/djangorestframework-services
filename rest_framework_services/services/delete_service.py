"""``DeleteService`` Protocol — typed shape for delete-action service callables."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

InputT = TypeVar("InputT", contravariant=True)
InstanceT = TypeVar("InstanceT", contravariant=True)
ResultT = TypeVar("ResultT", covariant=True)


class DeleteService(Protocol[InputT, InstanceT, ResultT]):
    """Structural shape for a delete-action service callable.

    Receives the resolved ``instance``. Most delete services return ``None``;
    if you need a response body, return a value and configure
    ``ServiceSpec.output_selector_spec`` with an ``output_serializer``
    (and optionally a re-fetch ``selector``).

    For *delete with payload* — when the spec carries an ``input_serializer``
    — bind ``InputT`` to your input dataclass and declare ``data`` on the
    service. ``data`` is optional in the Protocol (default ``Ellipsis``)
    so services that don't read a body can still match the shape by binding
    ``InputT`` to [`NoInput`][rest_framework_services.types.no_input.NoInput].

    See [`CreateService`][rest_framework_services.services.create_service.CreateService]
    for the extras-typing notes."""

    def __call__(
        self,
        *,
        instance: InstanceT,
        data: InputT = ...,
        **extras: Any,
    ) -> ResultT: ...
