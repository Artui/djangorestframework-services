"""Generic update endpoint backed by a user-defined service callable."""

from __future__ import annotations

from typing import Any, ClassVar

from rest_framework import status as drf_status
from rest_framework.generics import GenericAPIView
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin


class ServiceUpdateView(MutationFlowMixin, GenericAPIView):
    """``PUT`` / ``PATCH`` endpoint that runs a service callable.

    The instance to update is fetched via DRF's ``get_object()`` (so set
    ``queryset`` and ``lookup_field`` on the subclass), or by overriding
    ``get_object()`` for custom resolution.

    Configure by setting ``spec`` to a :class:`ServiceSpec`. The spec's
    ``success_status`` defaults to ``200 OK`` when unset.
    """

    spec: ClassVar[ServiceSpec | None] = None

    def put(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._run(request, partial=False)

    def patch(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._run(request, partial=True)

    def _run(self, request: Request, *, partial: bool) -> Response:
        spec: ServiceSpec | None = self.spec
        if spec is None:
            raise NotImplementedError(
                f"{type(self).__name__} requires a `spec` (ServiceSpec) attribute."
            )
        return self._run_mutation(
            request,
            service=spec.service,
            input_serializer=spec.input_serializer,
            output_serializer=spec.output_serializer,
            output_selector=spec.output_selector,
            atomic=spec.atomic,
            success_status=spec.success_status or drf_status.HTTP_200_OK,
            instance=self.get_object(),
            partial=partial,
        )
