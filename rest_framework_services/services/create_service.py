"""Lenient ``CreateService`` Protocol — opt-in shape for create services."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from rest_framework.request import Request

from rest_framework_services.services.utils import UserT

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT", covariant=True)


class CreateService(Protocol[InputT, ResultT]):
    """Structural shape for a create-action service callable.

    Lenient by design: ``**kwargs: Any`` is the escape hatch that lets the
    framework pass any extras (URL kwargs, ``ServiceSpec.kwargs`` /
    ``get_service_kwargs`` returns) without requiring the service to declare
    them. Services may freely omit parameters they do not need; the framework
    inspects the signature and only passes what is declared.

    Annotate against this Protocol for IDE / type-checker help on the *known*
    pool keys. For full enforcement (no ``**kwargs`` escape hatch), use
    :class:`StrictCreateService`.
    """

    def __call__(
        self,
        *,
        data: InputT,
        request: Request,
        user: UserT,
        **kwargs: Any,
    ) -> ResultT: ...
