"""``destroy`` action backed by a service callable."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from rest_framework import status as drf_status
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin


class ServiceDestroyMixin(MutationFlowMixin):
    """Provides the ``destroy`` action.

    Looks up the instance via DRF's ``get_object()``. Reads its config from
    ``service_specs["destroy"]``; when that key is absent the action raises
    ``MethodNotAllowed``.
    """

    # Provided at runtime by ``GenericAPIView`` / ``GenericViewSet``.
    get_object: Callable[..., Any]

    service_specs: ClassVar[Mapping[str, Callable[..., Any] | ServiceSpec]] = {}

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        spec = self.service_specs.get("destroy")
        if not isinstance(spec, ServiceSpec):
            raise MethodNotAllowed("DELETE")
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
