"""``update`` and ``partial_update`` actions backed by a service callable."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework import status as drf_status
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.viewsets.utils import _ActionSpecsMixin


class ServiceUpdateMixin(MutationFlowMixin, _ActionSpecsMixin):
    """Provides ``update`` (``PUT``) and ``partial_update`` (``PATCH``) actions.

    Looks up the instance via DRF's ``get_object()``. Reads its config from
    ``action_specs["update"]``; when that key is absent both actions raise
    ``MethodNotAllowed``. A non-:class:`ServiceSpec` entry raises
    :exc:`~django.core.exceptions.ImproperlyConfigured`.
    """

    # Provided at runtime by ``GenericAPIView`` / ``GenericViewSet``.
    get_object: Callable[..., Any]

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._update(request, partial=False)

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._update(request, partial=True)

    def _update(self, request: Request, *, partial: bool) -> Response:
        entry = self.action_specs.get("update")
        if entry is None:
            raise MethodNotAllowed("PATCH" if partial else "PUT")
        if not isinstance(entry, ServiceSpec):
            raise ImproperlyConfigured(
                f"action_specs['update'] must be a ServiceSpec, got "
                f"{type(entry).__name__}. "
                "Use ServiceSpec(service=...) for write actions."
            )
        return self._run_mutation(
            request,
            service=entry.service,
            input_serializer=entry.input_serializer,
            output_serializer=entry.output_serializer,
            output_selector=entry.output_selector,
            atomic=entry.atomic,
            success_status=entry.success_status or drf_status.HTTP_200_OK,
            instance=self.get_object(),
            partial=partial,
        )
