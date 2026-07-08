"""``ServiceAutoSchema`` — drf-spectacular ``AutoSchema`` aware of ``ServiceSpec``.

Importing this module pulls in ``drf-spectacular``; it is therefore *not*
loaded by the package's top-level ``__init__.py``. Users opt in via
:func:`rest_framework_services.openapi.enable_openapi`.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.contrib.django_filters import DjangoFilterExtension
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiResponse

from rest_framework_services.openapi._resolve import (
    resolve_selector_spec,
    resolve_service_spec,
)
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
      ``spec.output_selector_spec.output_serializer`` (or rendered as a
      no-content response when unset and the action's default status is 204).
    - A ``422`` response documenting :class:`ServiceErrorSerializer` is
      attached when the operation can surface a ``ServiceError``. By default
      that is gated on whether the operation validates input
      (``spec.input_serializer is not None``), so a no-input mutation (e.g. a
      plain delete) carries no spurious 422; ``spec.document_service_error``
      forces the decision either way.
    - Partial-update actions instantiate the request serializer with
      ``partial=True`` so optional-on-PATCH semantics show up correctly.
      ``spec.partial`` overrides the action-derived flag with the same
      precedence the runtime dispatch applies: ``partial=True`` documents a
      partial body on any verb, ``partial=False`` keeps the full-update
      schema (required fields intact) even under PATCH.

    Read surfaces (``Selector*View`` and the read-side action mixins) lean on
    the base ``AutoSchema`` for their body: the ``output_serializer`` is
    already wired through ``get_serializer_class()`` and that's what
    drf-spectacular reads natively. The one addition is
    :meth:`_get_filter_parameters`, which contributes the query parameters for
    a ``SelectorSpec.filter_set`` — the FilterSet the selector carries on the
    spec instead of on the view — so moving a FilterSet from a view-level
    ``filterset_class`` + ``DjangoFilterBackend`` onto ``spec.filter_set``
    leaves the generated OpenAPI unchanged.

    User-supplied ``@extend_schema`` annotations always win — they're
    consulted upstream of ``get_request_serializer`` / ``get_response_serializers``,
    and ``@extend_schema(parameters=...)`` overrides merge over the filter
    parameters exactly as they do for a view-level FilterSet.
    """

    def get_request_serializer(self) -> Any:
        spec = resolve_service_spec(self.view)
        if spec is not None:
            cls = to_serializer_class(spec.input_serializer)
            if cls is not None:
                # ``spec.partial`` overrides the action-derived flag, the
                # same precedence the runtime dispatch applies.
                if spec.partial is not None:
                    partial = spec.partial
                else:
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
            spec = resolve_service_spec(self.view)
            if spec is not None and spec.input_serializer is not None:
                saved = self.method
                self.method = "POST"
                try:
                    return super()._get_request_body(direction)
                finally:
                    self.method = saved
        if self.method == "PATCH":
            # spectacular unconditionally re-stamps ``partial=True`` on PATCH
            # request serializers ("we simulate what DRF is doing"), which
            # would override a forced ``spec.partial=False``. Same swap trick:
            # presenting the call as PUT keeps the full-update schema.
            spec = resolve_service_spec(self.view)
            if spec is not None and spec.partial is False and spec.input_serializer is not None:
                saved = self.method
                self.method = "PUT"
                try:
                    return super()._get_request_body(direction)
                finally:
                    self.method = saved
        return super()._get_request_body(direction)

    def get_response_serializers(self) -> Any:
        spec = resolve_service_spec(self.view)
        if spec is None:
            return super().get_response_serializers()
        status = spec.success_status or default_status(self.view)
        output_serializer = (
            spec.output_selector_spec.output_serializer
            if spec.output_selector_spec is not None
            else None
        )
        out_cls = to_serializer_class(output_serializer)
        # spectacular accepts a Serializer subclass *or* an OpenApiResponse
        # for each status entry; mix them depending on whether a response
        # body is configured.
        success: Any = out_cls if out_cls is not None else OpenApiResponse(description="")
        responses: dict[Any, Any] = {status: success}
        # Gate the 422 ServiceError response: ``document_service_error`` forces
        # it when set, otherwise default to documenting it only for operations
        # that validate input (a no-input delete then carries no spurious 422).
        document_422 = (
            spec.document_service_error
            if spec.document_service_error is not None
            else spec.input_serializer is not None
        )
        if document_422:
            responses[422] = ServiceErrorSerializer
        return responses

    def _get_filter_parameters(self) -> list[Any]:
        # The base emits parameters for the view's ``filter_backends`` (e.g. a
        # view-level ``DjangoFilterBackend``). A selector that declares its
        # FilterSet on ``spec.filter_set`` instead must drop
        # ``DjangoFilterBackend`` (the ``as_view()`` guard forbids running
        # both), so it is invisible to that path and its filter query
        # parameters would silently disappear from the schema. Contribute them
        # from the spec so a view→spec FilterSet move leaves the OpenAPI
        # document unchanged.
        parameters = super()._get_filter_parameters()
        # Mirror the base's list-only gate: ``get_filter_backends()`` returns
        # nothing for a detail (retrieve) operation, so a view-level FilterSet
        # documents no parameters there. Matching that keeps parity exact for
        # RETRIEVE specs too — both configurations emit an empty set — even
        # though the runtime still shapes + filters the retrieve queryset
        # before ``.first()``.
        if not self._is_list_view():
            return parameters
        spec = resolve_selector_spec(self.view)
        if spec is None or spec.selector is None or spec.filter_set is None:
            return parameters
        return parameters + _filter_set_parameters(self, spec.filter_set)


class _SpecFilterBackend:
    """Minimal stand-in exposing the one method ``DjangoFilterExtension`` calls.

    The extension resolves its FilterSet solely through
    ``self.target.get_filterset_class(view, queryset)``. Returning
    ``filter_set`` directly reproduces what a real ``DjangoFilterBackend`` with
    ``view.filterset_class`` set would return — so the extension emits
    identical parameters — without importing ``django-filter`` or mutating the
    inspected view.
    """

    def __init__(self, filter_set: Any) -> None:
        self._filter_set = filter_set

    def get_filterset_class(self, view: Any, queryset: Any = None) -> Any:
        return self._filter_set


def _filter_set_parameters(auto_schema: AutoSchema, filter_set: Any) -> list[Any]:
    """OpenAPI query parameters for a spec-level ``filter_set``.

    Reuses drf-spectacular's own ``DjangoFilterExtension`` so the output is
    byte-identical to a view-level ``filterset_class`` + ``DjangoFilterBackend``
    (same param names, types, enums, ordering enum + description, style /
    explode, required flags) and stays in lockstep with drf-spectacular across
    versions. The extension imports ``django-filter`` only lazily (inside its
    per-field resolution), so it is imported at module top like the rest of
    the drf-spectacular surface.

    Duck-typed guard: ``SelectorSpec.filter_set`` is applied by the
    ``(data, queryset) -> .qs`` contract and need not be a real ``FilterSet``.
    Anything without ``base_filters`` (e.g. a filter_set carried without the
    optional ``[filter]`` extra installed) degrades to no parameters rather
    than raising — the same posture ``filterset_to_json_schema`` takes on the
    JSON-Schema path.
    """
    if not hasattr(filter_set, "base_filters"):
        return []
    extension = DjangoFilterExtension(_SpecFilterBackend(filter_set))
    return list(extension.get_schema_operation_parameters(auto_schema))
