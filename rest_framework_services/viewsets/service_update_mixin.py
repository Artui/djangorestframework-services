"""``update`` and ``partial_update`` actions backed by a service callable."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from rest_framework import status as drf_status
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin


class ServiceUpdateMixin(MutationFlowMixin):
    """Provides ``update`` (``PUT``) and ``partial_update`` (``PATCH``) actions.

    Looks up the instance via DRF's ``get_object()``. Reads its config from
    ``service_specs["update"]``; when that key is absent both actions raise
    ``MethodNotAllowed``.
    """

    # Provided at runtime by ``GenericAPIView`` / ``GenericViewSet``.
    get_object: Callable[..., Any]

    service_specs: ClassVar[Mapping[str, Callable[..., Any] | ServiceSpec]] = {}

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._update(request, partial=False)

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._update(request, partial=True)

    def _update(self, request: Request, *, partial: bool) -> Response:
        spec = self.service_specs.get("update")
        if not isinstance(spec, ServiceSpec):
            raise MethodNotAllowed("PATCH" if partial else "PUT")
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
