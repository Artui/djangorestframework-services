"""Generic delete endpoint backed by a user-defined service callable."""

from __future__ import annotations

from typing import Any, ClassVar

from rest_framework import status as drf_status
from rest_framework.generics import GenericAPIView
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin


class ServiceDeleteView(MutationFlowMixin, GenericAPIView):
    """``DELETE`` endpoint that runs a service callable.

    Configure by setting ``spec`` to a :class:`ServiceSpec`. The spec's
    ``input_serializer`` is optional (for delete-with-payload patterns
    such as a deletion reason); ``success_status`` defaults to
    ``204 No Content``; set ``output_serializer`` on the spec to render
    a body instead.
    """

    spec: ClassVar[ServiceSpec | None] = None

    def delete(self, request: Request, *args: Any, **kwargs: Any) -> Response:
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
            success_status=spec.success_status or drf_status.HTTP_204_NO_CONTENT,
            instance=self.get_object(),
        )
