"""``ServiceAutoSchema`` — drf-spectacular ``AutoSchema`` aware of ``ServiceSpec``.

Importing this module pulls in ``drf-spectacular``; it is therefore *not*
loaded by the package's top-level ``__init__.py``. Users opt in via
:func:`rest_framework_services.openapi.enable_openapi`.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiResponse

from rest_framework_services.openapi._resolve import resolve_spec
from rest_framework_services.openapi._to_serializer import to_serializer_class
from rest_framework_services.openapi.service_error_serializer import (
    ServiceErrorSerializer,
)
from rest_framework_services.openapi.utils import default_status


class ServiceAutoSchema(AutoSchema):
    """``AutoSchema`` that derives its request / response from a ``ServiceSpec``.

    For mutation surfaces (standalone ``Service*View`` classes, the
    ``ServiceViewSet`` action mixins, and ``@service_action``):

    - Request body is derived from ``spec.input_serializer`` (a bare
      dataclass is auto-wrapped in :class:`DataclassSerializer`).
    - Response body for the success status is derived from
      ``spec.output_serializer`` (or rendered as a no-content response when
      unset and the action's default status is 204).
    - A ``422`` response documenting :class:`ServiceErrorSerializer` is
      attached so consumers know about the ``ServiceError`` contract.
    - Partial-update actions instantiate the request serializer with
      ``partial=True`` so optional-on-PATCH semantics show up correctly.

    Read surfaces (``Selector*View`` and the read-side action mixins) are
    left to the base ``AutoSchema``: their ``output_serializer`` is already
    wired through ``get_serializer_class()`` and that's what
    drf-spectacular reads natively.

    User-supplied ``@extend_schema`` annotations always win — they're
    consulted upstream of ``get_request_serializer`` / ``get_response_serializers``.
    """

    def get_request_serializer(self) -> Any:
        spec = resolve_spec(self.view)
        if spec is not None:
            cls = to_serializer_class(spec.input_serializer)
            if cls is not None:
                partial = getattr(self.view, "action", None) == "partial_update"
                return cls(partial=partial)
        return super().get_request_serializer()

    def _get_request_body(self, direction: str = "request") -> Any:
        # drf-spectacular hard-codes the allowed verbs to PUT/PATCH/POST in
        # ``AutoSchema._get_request_body``. RFC 7231 permits a body on
        # DELETE, and the framework's ``ServiceDeleteView`` /
        # ``ServiceDestroyMixin`` use it for delete-with-payload. When the
        # spec declares an ``input_serializer`` we coax spectacular into
        # emitting the body by swapping ``self.method`` for the duration
        # of the super call.
        if self.method == "DELETE":
            spec = resolve_spec(self.view)
            if spec is not None and spec.input_serializer is not None:
                saved = self.method
                self.method = "POST"
                try:
                    return super()._get_request_body(direction)
                finally:
                    self.method = saved
        return super()._get_request_body(direction)

    def get_response_serializers(self) -> Any:
        spec = resolve_spec(self.view)
        if spec is None:
            return super().get_response_serializers()
        status = spec.success_status or default_status(self.view)
        out_cls = to_serializer_class(spec.output_serializer)
        # spectacular accepts a Serializer subclass *or* an OpenApiResponse
        # for each status entry; mix them depending on whether a response
        # body is configured.
        success: Any = out_cls if out_cls is not None else OpenApiResponse(description="")
        return {
            status: success,
            422: ServiceErrorSerializer,
        }
