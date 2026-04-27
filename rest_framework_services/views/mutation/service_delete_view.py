"""Generic delete endpoint backed by a user-defined service callable."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from rest_framework import status as drf_status
from rest_framework.generics import GenericAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.views.utils import get_class_attr


class ServiceDeleteView(MutationFlowMixin, GenericAPIView):
    """``DELETE`` endpoint that runs a service callable.

    Optionally accepts a request body (``input_dataclass``) for delete-with-
    payload patterns (e.g. a deletion reason). By default returns ``204 No
    Content``; configure ``output_serializer`` to render a body.
    """

    service: ClassVar[Callable[..., Any] | None] = None
    input_dataclass: ClassVar[type | None] = None
    output_serializer: ClassVar[type[Serializer] | None] = None
    output_selector: ClassVar[Callable[..., Any] | None] = None
    atomic: ClassVar[bool] = True
    success_status: ClassVar[int] = drf_status.HTTP_204_NO_CONTENT

    def delete(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        service: Callable[..., Any] | None = get_class_attr(self, "service")
        if service is None:
            raise NotImplementedError(f"{type(self).__name__} requires a `service` callable.")
        return self._run_mutation(
            request,
            service=service,
            input_dataclass=self.input_dataclass,
            output_serializer=self.output_serializer,
            output_selector=get_class_attr(self, "output_selector"),
            atomic=self.atomic,
            success_status=self.success_status,
            instance=self.get_object(),
        )
