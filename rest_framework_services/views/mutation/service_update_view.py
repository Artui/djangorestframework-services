"""Generic update endpoint backed by a user-defined service callable."""

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


class ServiceUpdateView(MutationFlowMixin, GenericAPIView):
    """``PUT`` / ``PATCH`` endpoint that runs a service callable.

    The instance to update is resolved via ``spec.instance_selector_spec``
    when set — no ``queryset`` / ``lookup_field`` required on the subclass —
    falling back to DRF's ``get_object()`` (set ``queryset`` and
    ``lookup_field``, or override ``get_object()``).

    Configure by setting ``spec`` to a :class:`ServiceSpec`. The spec's
    ``success_status`` defaults to ``200 OK`` when unset. Both verbs share
    the one spec, so a forced ``spec.partial`` applies to PUT *and* PATCH —
    set ``http_method_names = ["patch"]`` for a PATCH-only endpoint.
    """

    spec: ClassVar[ServiceSpec | None] = None

    @classmethod
    def as_view(cls, **initkwargs: Any) -> Any:
        validate_mutation_view_spec(cls, has_instance=True)
        return super().as_view(**initkwargs)

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
            spec,
            instance=resolve_mutation_instance(self, spec),
            success_status=spec.success_status or drf_status.HTTP_200_OK,
            render_instance_on_none=True,
            partial=partial,
        )
