"""``create`` action backed by a service callable."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from rest_framework import status as drf_status
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin


class ServiceCreateMixin(MutationFlowMixin):
    """Provides the ``create`` action; reads its config from ``service_specs``.

    Set ``service_specs["create"]`` to a :class:`ServiceSpec`. When the
    ``"create"`` key is absent (or holds a non-:class:`ServiceSpec` entry)
    the action raises ``MethodNotAllowed``.
    """

    service_specs: ClassVar[Mapping[str, Callable[..., Any] | ServiceSpec]] = {}

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        spec = self.service_specs.get("create")
        if not isinstance(spec, ServiceSpec):
            raise MethodNotAllowed("POST")
        return self._run_mutation(
            request,
            service=spec.service,
            input_serializer=spec.input_serializer,
            output_serializer=spec.output_serializer,
            output_selector=spec.output_selector,
            atomic=spec.atomic,
            success_status=spec.success_status or drf_status.HTTP_201_CREATED,
            instance=None,
        )
