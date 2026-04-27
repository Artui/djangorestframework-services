"""``create`` action backed by a service callable."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from rest_framework import status as drf_status
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.views.utils import get_class_attr


class ServiceCreateMixin(MutationFlowMixin):
    """Provides the ``create`` action; expects ``create_service`` to be set.

    When ``create_service`` is ``None``, the action raises ``MethodNotAllowed``.

    Configure via class attributes:

    - ``create_service`` — the callable invoked to create the resource.
    - ``create_input_dataclass`` — request-body validation; ``None`` means
      no body is required.
    - ``create_output_serializer`` — DRF serializer for the response.
    - ``create_output_selector`` — optional callable invoked with the
      service result to fetch what to render.
    - ``create_atomic`` — wrap in ``transaction.atomic()`` (default ``True``).
    """

    create_service: ClassVar[Callable[..., Any] | None] = None
    create_input_dataclass: ClassVar[type | None] = None
    create_output_serializer: ClassVar[type[Serializer] | None] = None
    create_output_selector: ClassVar[Callable[..., Any] | None] = None
    create_atomic: ClassVar[bool] = True

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        service: Callable[..., Any] | None = get_class_attr(self, "create_service")
        if service is None:
            raise MethodNotAllowed("POST")
        return self._run_mutation(
            request,
            service=service,
            input_dataclass=self.create_input_dataclass,
            output_serializer=self.create_output_serializer,
            output_selector=get_class_attr(self, "create_output_selector"),
            atomic=self.create_atomic,
            success_status=drf_status.HTTP_201_CREATED,
            instance=None,
        )
