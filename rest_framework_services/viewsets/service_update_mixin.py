"""``update`` and ``partial_update`` actions backed by a service callable."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rest_framework import status as drf_status
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.viewsets.utils import (
    _ActionSpecsMixin,
    resolve_action_service_spec,
)


class ServiceUpdateMixin(MutationFlowMixin, _ActionSpecsMixin):
    """Provides ``update`` (``PUT``) and ``partial_update`` (``PATCH``) actions.

    Looks up the instance via DRF's ``get_object()``. Reads its config from
    ``action_specs["update"]``; when that key is absent both actions raise
    :exc:`~rest_framework.exceptions.MethodNotAllowed`. A non-``ServiceSpec``
    entry raises :exc:`~django.core.exceptions.ImproperlyConfigured`.
    """

    # Provided at runtime by ``GenericAPIView`` / ``GenericViewSet``.
    get_object: Callable[..., Any]

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._update(request, partial=False)

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._update(request, partial=True)

    def _update(self, request: Request, *, partial: bool) -> Response:
        spec = resolve_action_service_spec(
            self.action_specs, "update", "PATCH" if partial else "PUT"
        )
        return self._run_mutation(
            request,
            spec,
            instance=self.get_object(),
            success_status=spec.success_status or drf_status.HTTP_200_OK,
            partial=partial,
        )
