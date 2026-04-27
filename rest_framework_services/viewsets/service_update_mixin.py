"""``update`` and ``partial_update`` actions backed by a service callable."""

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


class ServiceUpdateMixin(MutationFlowMixin):
    """Provides ``update`` (``PUT``) and ``partial_update`` (``PATCH``) actions.

    Looks up the instance via DRF's ``get_object()``. When ``update_service``
    is ``None``, the action raises ``MethodNotAllowed``.

    Configure via class attributes — see :class:`ServiceCreateMixin`.
    """

    # Provided at runtime by ``GenericAPIView`` / ``GenericViewSet``.
    get_object: Callable[..., Any]

    update_service: ClassVar[Callable[..., Any] | None] = None
    update_input_dataclass: ClassVar[type | None] = None
    update_output_serializer: ClassVar[type[Serializer] | None] = None
    update_output_selector: ClassVar[Callable[..., Any] | None] = None
    update_atomic: ClassVar[bool] = True

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._update(request, partial=False)

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self._update(request, partial=True)

    def _update(self, request: Request, *, partial: bool) -> Response:
        service: Callable[..., Any] | None = get_class_attr(self, "update_service")
        if service is None:
            raise MethodNotAllowed("PATCH" if partial else "PUT")
        return self._run_mutation(
            request,
            service=service,
            input_dataclass=self.update_input_dataclass,
            output_serializer=self.update_output_serializer,
            output_selector=get_class_attr(self, "update_output_selector"),
            atomic=self.update_atomic,
            success_status=drf_status.HTTP_200_OK,
            instance=self.get_object(),
            partial=partial,
        )
