"""Generic update endpoint backed by a user-defined service callable."""

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


class ServiceUpdateView(MutationFlowMixin, GenericAPIView):
    """``PUT`` / ``PATCH`` endpoint that runs a service callable.

    The instance to update is fetched via DRF's ``get_object()`` (so set
    ``queryset`` and ``lookup_field`` on the subclass), or by overriding
    ``get_object()`` for custom resolution.

    Configure via class attributes — see :class:`ServiceCreateView`.
    """

    service: ClassVar[Callable[..., Any] | None] = None
    input_serializer: ClassVar[type | None] = None
    output_serializer: ClassVar[type[Serializer] | None] = None
    output_selector: ClassVar[Callable[..., Any] | None] = None
    atomic: ClassVar[bool] = True
    success_status: ClassVar[int] = drf_status.HTTP_200_OK

    def put(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._run(request, partial=False)

    def patch(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._run(request, partial=True)

    def _run(self, request: Request, *, partial: bool) -> Response:
        service: Callable[..., Any] | None = get_class_attr(self, "service")
        if service is None:
            raise NotImplementedError(f"{type(self).__name__} requires a `service` callable.")
        return self._run_mutation(
            request,
            service=service,
            input_serializer=self.input_serializer,
            output_serializer=self.output_serializer,
            output_selector=get_class_attr(self, "output_selector"),
            atomic=self.atomic,
            success_status=self.success_status,
            instance=self.get_object(),
            partial=partial,
        )
