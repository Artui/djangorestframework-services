"""Generic create endpoint backed by a user-defined service callable."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from rest_framework import status as drf_status
from rest_framework.generics import GenericAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.views.utils import get_class_attr


class ServiceCreateView(MutationFlowMixin, GenericAPIView):
    """``POST`` endpoint that runs a service callable to create a resource.

    Configure via class attributes:

    - ``service`` — the callable to invoke (required).
    - ``input_serializer`` — request-body validation; accepts a dataclass
      type, a ``DataclassSerializer`` subclass, or any other ``Serializer``
      subclass (e.g. ``ModelSerializer``). ``None`` means the endpoint
      accepts an empty body.
    - ``output_serializer`` — DRF serializer used to render the response.
    - ``output_selector`` — optional callable invoked after the service to
      fetch the value to serialize.
    - ``atomic`` — wrap the service call in ``transaction.atomic()``
      (default ``True``).
    """

    service: ClassVar[Callable[..., Any] | None] = None
    input_serializer: ClassVar[type | None] = None
    output_serializer: ClassVar[type[Serializer] | None] = None
    output_selector: ClassVar[Callable[..., Any] | None] = None
    atomic: ClassVar[bool] = True
    success_status: ClassVar[int] = drf_status.HTTP_201_CREATED

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        service: Callable[..., Any] | None = get_class_attr(self, "service")
        if service is None:
            raise NotImplementedError(f"{type(self).__name__} requires a `service` callable.")
        return self._run_mutation(
            request,
            service=service,
            input_serializer=self.input_serializer,
            output_serializer=self.output_serializer,
            output_selector=get_class_attr(self, "output_selector"),
            atomic=self.atomic,
            success_status=self.success_status,
            instance=None,
        )
