"""Generic delete endpoint backed by a user-defined service callable."""

from __future__ import annotations

from typing import Any, ClassVar

from rest_framework import status as drf_status
from rest_framework.generics import GenericAPIView
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.views.mutation.utils import resolve_mutation_instance
from rest_framework_services.views.spec_validation import validate_mutation_view_spec


class ServiceDeleteView(MutationFlowMixin, GenericAPIView):
    """``DELETE`` endpoint that runs a service callable.

    The instance to delete is resolved via ``spec.instance_selector_spec``
    when set — no ``queryset`` / ``lookup_field`` required on the subclass —
    falling back to DRF's ``get_object()``.

    Configure by setting ``spec`` to a
    [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec]. The spec's
    ``input_serializer`` is optional (for delete-with-payload patterns such as a
    deletion reason); ``success_status`` defaults to ``204 No Content``; set
    ``output_selector_spec`` with an ``output_serializer`` on the spec to render a body
    instead."""

    spec: ClassVar[ServiceSpec | None] = None

    @classmethod
    def as_view(cls, **initkwargs: Any) -> Any:
        validate_mutation_view_spec(cls, has_instance=True)
        return super().as_view(**initkwargs)

    def delete(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        spec: ServiceSpec | None = self.spec
        if spec is None:
            raise NotImplementedError(
                f"{type(self).__name__} requires a `spec` (ServiceSpec) attribute."
            )
        return self._run_mutation(
            request,
            spec,
            instance=resolve_mutation_instance(self, spec),
            default_status=drf_status.HTTP_204_NO_CONTENT,
        )
