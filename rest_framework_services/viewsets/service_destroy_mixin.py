"""``destroy`` action backed by a service callable."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rest_framework import status as drf_status
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.views.mutation.utils import resolve_mutation_instance
from rest_framework_services.viewsets.utils import (
    _ActionSpecsMixin,
    resolve_action_service_spec,
)


class ServiceDestroyMixin(MutationFlowMixin, _ActionSpecsMixin):
    """Provides the ``destroy`` action.

    Looks up the instance via ``spec.instance_selector_spec`` when set,
    falling back to DRF's ``get_object()``. Reads its config from
    ``action_specs["destroy"]``; when that key is absent the action raises
    :exc:`~rest_framework.exceptions.MethodNotAllowed`. A non-``ServiceSpec``
    entry raises :exc:`~django.core.exceptions.ImproperlyConfigured`.
    """

    # Provided at runtime by ``GenericAPIView`` / ``GenericViewSet``.
    get_object: Callable[..., Any]

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        spec = resolve_action_service_spec(self.action_specs, "destroy", "DELETE")
        return self._run_mutation(
            request,
            spec,
            instance=resolve_mutation_instance(self, spec),
            default_status=drf_status.HTTP_204_NO_CONTENT,
        )
