"""``ServiceAutoSchema`` — drf-spectacular ``AutoSchema`` aware of ``ServiceSpec``.

Importing this module pulls in ``drf-spectacular``, so the package's top-level
``__init__.py`` does not load it; users opt in via
[`rest_framework_services.openapi.enable_openapi`][rest_framework_services.openapi.enable_openapi.enable_openapi].
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.contrib.django_filters import DjangoFilterExtension
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiResponse, PolymorphicProxySerializer

from rest_framework_services.can_present_nothing import can_present_nothing
from rest_framework_services.openapi._resolve import (
    resolve_polymorphic_spec,
    resolve_selector_spec,
    resolve_service_spec,
)
from rest_framework_services.openapi._to_serializer import to_serializer_class
from rest_framework_services.openapi.service_error_serializer import (
    ServiceErrorSerializer,
)
from rest_framework_services.openapi.utils import default_status
from rest_framework_services.views.mutation.utils import status_for_a_body


class ServiceAutoSchema(AutoSchema):
    """``AutoSchema`` that derives its request / response from a ``ServiceSpec``.

    On mutation surfaces (standalone ``Service*View`` classes, the
    ``ServiceViewSet`` action mixins, and ``@service_action``):

    - Request body from ``spec.input_serializer``, a bare dataclass being auto-wrapped
      in ``DataclassSerializer``, built with whatever partial flag ``spec.partial``
      resolves to so the documented body matches what actually validates.
    - Success response from ``spec.output_selector_spec.output_serializer``,
      under the status the runtime serves a body under, which is ``200`` where
      the status is ``204``; or, where no serializer is declared, a no-content
      response at the status itself.
    - A ``422`` documenting
      [`ServiceErrorSerializer`][rest_framework_services.openapi.service_error_serializer.ServiceErrorSerializer],
      as ``spec.document_service_error`` gates it.

    Read surfaces (``Selector*View`` and the read-side action mixins) lean on
    the base ``AutoSchema`` for their body: ``output_serializer`` is already
    wired through ``get_serializer_class()``, which is what drf-spectacular
    reads natively. The one addition is ``_get_filter_parameters``,
    contributing the query parameters for a ``SelectorSpec.filter_set``, so
    moving a FilterSet from a view-level ``filterset_class`` +
    ``DjangoFilterBackend`` onto ``spec.filter_set`` leaves the generated
    OpenAPI unchanged.

    User-supplied ``@extend_schema`` annotations always win, and
    ``@extend_schema(parameters=...)`` merges over the filter parameters exactly
    as it does for a view-level FilterSet.
    """

    def get_request_serializer(self) -> Any:
        poly = resolve_polymorphic_spec(self.view)
        if poly is not None:
            proxy = self._polymorphic_request_serializer(poly)
            if proxy is not None:
                return proxy
        spec = resolve_service_spec(self.view)
        if spec is not None:
            cls = to_serializer_class(spec.input_serializer)
            if cls is not None:
                if spec.partial is not None:
                    partial = spec.partial
                else:
                    partial = getattr(self.view, "action", None) == "partial_update"
                return cls(partial=partial)
        return super().get_request_serializer()

    def _polymorphic_request_serializer(self, poly: Any) -> Any:
        """Render a polymorphic action's request body as the variant union.

        A ``PolymorphicProxySerializer`` over each variant's ``input_serializer``
        with ``resource_type_field_name=None`` — an ``anyOf``, since the
        framework discriminates on payload content rather than a type field.
        Variants without an ``input_serializer`` contribute nothing; with none
        at all, defer to the base body.
        """
        serializers = [
            cls()
            for cls in (to_serializer_class(v.input_serializer) for v in poly.specs.values())
            if cls is not None
        ]
        if not serializers:
            return None
        view_name = type(self.view).__name__
        action_name = getattr(self.view, "action", "") or ""
        return PolymorphicProxySerializer(
            component_name=f"{view_name}_{action_name}_Request",
            serializers=serializers,
            resource_type_field_name=None,
        )

    def _get_request_body(self, direction: str = "request") -> Any:
        # drf-spectacular hard-codes the body verbs to PUT/PATCH/POST. RFC 7231
        # permits a body on DELETE and ``ServiceDeleteView`` /
        # ``ServiceDestroyMixin`` use it, so swap ``self.method`` for the
        # duration of the super call to coax the body out.
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
            # request serializers, which would override a forced
            # ``spec.partial=False``; presenting the call as PUT keeps the
            # full-update schema.
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
        # A callable ``success_status`` keys on the per-request result, so it
        # can't be resolved statically; document the action default instead.
        status = (
            spec.success_status
            if isinstance(spec.success_status, int)
            else default_status(self.view)
        )
        output_serializer = (
            spec.output_selector_spec.output_serializer
            if spec.output_selector_spec is not None
            else None
        )
        out_cls = to_serializer_class(output_serializer)
        # spectacular accepts a Serializer subclass *or* an OpenApiResponse per
        # status entry; mix them by whether a response body is configured. A
        # declared serializer is a body, and the renderers answer a body under
        # ``status_for_a_body``, so the schema asks it the same question rather
        # than restating the answer: a destroy presenting a row, single-row or
        # bulk, is documented as the ``200`` it is served as. Held by
        # test_a_destroy_documents_the_response_it_serves.
        #
        # With no serializer declared there is no body the schema can describe,
        # and whether the runtime sends one is decided by what the service
        # returns, which nothing the schema reads declares. So the no-content
        # response is documented at the status itself, which is what a destroy
        # whose service returns ``None`` is answered with. A raw value returned
        # instead, a count say, is served as a body under ``status_for_a_body``
        # that the schema does not show. Held by
        # test_an_undeclared_body_is_documented_as_no_content.
        responses: dict[Any, Any] = (
            {status_for_a_body(status): out_cls}
            if out_cls is not None
            else {status: OpenApiResponse(description="")}
        )
        # A declared body is not always sent. Where dispatch may present nothing,
        # a re-read finding no row say, the renderers answer an empty ``204``
        # rather than a row of blank fields, so the schema documents that response
        # beside the body, asking ``can_present_nothing`` as every transport does.
        # The body is never documented under ``204`` itself (``status_for_a_body``),
        # so the two never collide. Without a serializer the empty response is
        # already the one documented, at its status. One arc to coverage, so each
        # operand names its test: the serializer,
        # test_an_undeclared_body_is_documented_at_its_status_alone, and the
        # question, test_a_create_that_cannot_present_nothing_documents_no_204.
        # test_a_create_that_may_present_nothing_documents_its_empty_204 holds
        # the branch.
        if out_cls is not None and can_present_nothing(spec):
            responses[204] = OpenApiResponse(description="")
        document_422 = (
            spec.document_service_error
            if spec.document_service_error is not None
            else spec.input_serializer is not None
        )
        if document_422:
            responses[422] = ServiceErrorSerializer
        return responses

    def _get_filter_parameters(self) -> list[Any]:
        # A spec-level FilterSet must drop ``DjangoFilterBackend`` (the
        # ``as_view()`` guard forbids running both), so it is invisible to the
        # base's backend-driven path and its parameters would silently vanish.
        parameters = super()._get_filter_parameters()
        # Mirror the base's list-only gate: ``get_filter_backends()`` returns
        # nothing for a detail operation, so a view-level FilterSet documents no
        # parameters there either. Parity stays exact for RETRIEVE specs even
        # though the runtime still shapes and filters the retrieve queryset
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
    ``self.target.get_filterset_class(view, queryset)``, so returning
    ``filter_set`` reproduces what a real ``DjangoFilterBackend`` with
    ``view.filterset_class`` would — without importing ``django-filter`` or
    mutating the inspected view.
    """

    def __init__(self, filter_set: Any) -> None:
        self._filter_set = filter_set

    def get_filterset_class(self, view: Any, queryset: Any = None) -> Any:
        return self._filter_set


def _filter_set_parameters(auto_schema: AutoSchema, filter_set: Any) -> list[Any]:
    """OpenAPI query parameters for a spec-level ``filter_set``.

    Reuses drf-spectacular's own ``DjangoFilterExtension``, so the output is
    byte-identical to a view-level ``filterset_class`` + ``DjangoFilterBackend``
    (same param names, types, enums, style / explode, required flags) and stays
    in lockstep across versions. The extension imports ``django-filter`` only
    lazily, inside its per-field resolution, so it is safe to import at module
    top with the rest of the drf-spectacular surface.

    ``SelectorSpec.filter_set`` is applied by the ``(data, queryset) -> .qs``
    contract and need not be a real ``FilterSet``, so anything without
    ``base_filters`` — a filter_set carried without the ``[filter]`` extra
    installed — degrades to no parameters rather than raising, the same posture
    ``filterset_to_json_schema`` takes.
    """
    if not hasattr(filter_set, "base_filters"):
        return []
    extension = DjangoFilterExtension(_SpecFilterBackend(filter_set))
    return list(extension.get_schema_operation_parameters(auto_schema))
