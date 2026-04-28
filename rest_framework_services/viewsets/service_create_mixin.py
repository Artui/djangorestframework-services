"""``create`` action backed by a service callable."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework import status as drf_status
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.viewsets.utils import _ActionSpecsMixin


class ServiceCreateMixin(MutationFlowMixin, _ActionSpecsMixin):
    """Provides the ``create`` action; reads its config from ``action_specs``.

    Set ``action_specs["create"]`` to a :class:`ServiceSpec`. When the
    ``"create"`` key is absent the action raises ``MethodNotAllowed``. A
    non-:class:`ServiceSpec` entry (e.g. a :class:`SelectorSpec`) raises
    :exc:`~django.core.exceptions.ImproperlyConfigured`.
    """

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        entry = self.action_specs.get("create")
        if entry is None:
            raise MethodNotAllowed("POST")
        if not isinstance(entry, ServiceSpec):
            raise ImproperlyConfigured(
                f"action_specs['create'] must be a ServiceSpec, got "
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
            success_status=entry.success_status or drf_status.HTTP_201_CREATED,
            instance=None,
        )
