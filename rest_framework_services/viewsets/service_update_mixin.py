"""``update`` and ``partial_update`` actions backed by a service callable."""

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


class ServiceUpdateMixin(MutationFlowMixin, _ActionSpecsMixin):
    """Provides ``update`` (``PUT``) and ``partial_update`` (``PATCH``) actions.

    Looks up the instance via ``spec.instance_selector_spec`` when set,
    falling back to DRF's ``get_object()``. ``PUT`` reads its config from
    ``action_specs["update"]``; ``PATCH`` reads ``action_specs["partial_update"]``
    first and falls back to ``"update"`` — defining *only*
    ``"partial_update"`` yields a PATCH-only endpoint (PUT raises
    ``MethodNotAllowed``). When neither key
    resolves, both actions raise
    ``MethodNotAllowed``. A non-``ServiceSpec``
    entry raises ``ImproperlyConfigured``.
    """

    # Provided at runtime by ``GenericAPIView`` / ``GenericViewSet``.
    get_object: Callable[..., Any]

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._update(request, partial=False)

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._update(request, partial=True)

    def _update(self, request: Request, *, partial: bool) -> Response:
        spec = resolve_action_service_spec(
            self.action_specs,
            "partial_update" if partial else "update",
            "PATCH" if partial else "PUT",
            view=self,
        )
        return self._run_mutation(
            request,
            spec,
            instance=resolve_mutation_instance(self, spec),
            default_status=drf_status.HTTP_200_OK,
            render_instance_on_none=True,
            partial=partial,
        )
