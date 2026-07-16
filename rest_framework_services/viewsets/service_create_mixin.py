"""``create`` action backed by a service callable."""

from __future__ import annotations

from typing import Any

from rest_framework import status as drf_status
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.viewsets.utils import (
    _ActionSpecsMixin,
    resolve_action_service_spec,
)


class ServiceCreateMixin(MutationFlowMixin, _ActionSpecsMixin):
    """Provides the ``create`` action; reads its config from ``action_specs``.

    Set ``action_specs["create"]`` to a
    :class:`~rest_framework_services.types.service_spec.ServiceSpec`.
    When the ``"create"`` key is absent the action raises
    :exc:`~rest_framework.exceptions.MethodNotAllowed`. A non-``ServiceSpec``
    entry (e.g. a :class:`~rest_framework_services.types.selector_spec.SelectorSpec`)
    raises :exc:`~django.core.exceptions.ImproperlyConfigured`.
    """

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        spec = resolve_action_service_spec(self.action_specs, "create", "POST")
        return self._run_mutation(
            request,
            spec,
            instance=None,
            default_status=drf_status.HTTP_201_CREATED,
        )
