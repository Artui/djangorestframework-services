"""``destroy`` action backed by a service callable."""

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


class ServiceDestroyMixin(MutationFlowMixin):
    """Provides the ``destroy`` action.

    Looks up the instance via DRF's ``get_object()``. When ``destroy_service``
    is ``None``, the action raises ``MethodNotAllowed``.

    Configure via class attributes — see :class:`ServiceCreateMixin`.
    """

    # Provided at runtime by ``GenericAPIView`` / ``GenericViewSet``.
    get_object: Callable[..., Any]

    destroy_service: ClassVar[Callable[..., Any] | None] = None
    destroy_input_dataclass: ClassVar[type | None] = None
    destroy_output_serializer: ClassVar[type[Serializer] | None] = None
    destroy_output_selector: ClassVar[Callable[..., Any] | None] = None
    destroy_atomic: ClassVar[bool] = True

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        service: Callable[..., Any] | None = get_class_attr(self, "destroy_service")
        if service is None:
            raise MethodNotAllowed("DELETE")
        return self._run_mutation(
            request,
            service=service,
            input_dataclass=self.destroy_input_dataclass,
            output_serializer=self.destroy_output_serializer,
            output_selector=get_class_attr(self, "destroy_output_selector"),
            atomic=self.destroy_atomic,
            success_status=drf_status.HTTP_204_NO_CONTENT,
            instance=self.get_object(),
        )
