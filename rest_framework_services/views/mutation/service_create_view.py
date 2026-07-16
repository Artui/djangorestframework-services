"""Generic create endpoint backed by a user-defined service callable."""

from __future__ import annotations

from typing import Any, ClassVar

from rest_framework import status as drf_status
from rest_framework.generics import GenericAPIView
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.views.spec_validation import validate_mutation_view_spec


class ServiceCreateView(MutationFlowMixin, GenericAPIView):
    """``POST`` endpoint that runs a service callable to create a resource.

    Configure by setting ``spec`` to a :class:`ServiceSpec`. The spec's
    ``success_status`` defaults to ``201 Created`` when unset.
    """

    spec: ClassVar[ServiceSpec | None] = None

    @classmethod
    def as_view(cls, **initkwargs: Any) -> Any:
        validate_mutation_view_spec(cls, has_instance=False)
        return super().as_view(**initkwargs)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        spec: ServiceSpec | None = self.spec
        if spec is None:
            raise NotImplementedError(
                f"{type(self).__name__} requires a `spec` (ServiceSpec) attribute."
            )
        return self._run_mutation(
            request,
            spec,
            instance=None,
            default_status=drf_status.HTTP_201_CREATED,
        )
