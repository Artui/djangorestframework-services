"""Per-action serializer dispatch for ViewSets driven by ``action_specs``."""

from __future__ import annotations

from typing import Any

from rest_framework.serializers import BaseSerializer, Serializer

from rest_framework_services.dispatch.renderable_serializer_class import renderable_serializer_class
from rest_framework_services.types.polymorphic_service_spec import PolymorphicServiceSpec
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.viewsets.utils import (
    _ActionSpecsMixin,
    resolve_action_spec_entry,
)


class _NoBody(Serializer):
    """Describes a body of no fields: what a service taking no input accepts.

    One class for the process, not one per OPTIONS request.
    """


class ActionSerializerResolver(_ActionSpecsMixin):
    """Resolve ``get_serializer_class()`` from ``action_specs``.

    Reads the active action's entry: a
    [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] supplies
    ``output_serializer``, a
    [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] supplies
    ``output_selector_spec.output_serializer``. Falls back to DRF's
    ``serializer_class``, raising the usual ``AssertionError`` when neither is set.

    OPTIONS is answered under DRF's implicit ``"metadata"`` action, once per
    POST / PUT it describes. There the spec is the one that method routes to,
    from ``action_specs`` or stamped by ``@service_action``, and what it answers
    with is the body that method accepts:

    - a ``ServiceSpec`` with an ``input_serializer`` answers with it (a bare
      dataclass wrapped as it is for rendering), ahead of ``serializer_class``;
    - a
      [`PolymorphicServiceSpec`][rest_framework_services.types.polymorphic_service_spec.PolymorphicServiceSpec]
      answers with its variants' ``input_serializer`` when they all declare the
      same one. Its discriminator is never asked: an OPTIONS request carries no
      body to discriminate;
    - otherwise ``serializer_class`` when the view sets one, else a serializer
      with no fields, which describes a method that accepts no body.

    Every other action resolves exactly as it did without OPTIONS in the
    picture.

    Example:

        class InvoiceViewSet(ActionSerializerResolver, GenericViewSet):
            action_specs = {
                "list": SelectorSpec(
                    kind=SelectorKind.LIST, output_serializer=InvoiceListSerializer,
                ),
                "retrieve": SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=InvoiceDetailSerializer,
                ),
            }
    """

    def get_serializer_class(self) -> type[BaseSerializer[Any]]:
        if self.action == "metadata":
            described = self._described_action_spec()
            if isinstance(described, ServiceSpec | PolymorphicServiceSpec):
                return self._describe_input(described)
        # Same ``"partial_update"`` → ``"update"`` fallback as dispatch and
        # ``get_permissions``, so the three sites agree. Deliberately not the
        # handler stamp ``get_permissions`` also reads: that would change what
        # a decorated action's ``get_serializer()`` returns outside OPTIONS.
        spec = resolve_action_spec_entry(self.action_specs, self.action)
        if isinstance(spec, SelectorSpec) and spec.output_serializer is not None:
            return renderable_serializer_class(spec.output_serializer)
        if (
            isinstance(spec, ServiceSpec)
            and spec.output_selector_spec is not None
            and spec.output_selector_spec.output_serializer is not None
        ):
            return renderable_serializer_class(spec.output_selector_spec.output_serializer)
        return super().get_serializer_class()  # ty: ignore[unresolved-attribute]

    def _describe_input(
        self, spec: ServiceSpec[Any, Any, Any] | PolymorphicServiceSpec
    ) -> type[BaseSerializer[Any]]:
        declared = _declared_input(spec)
        if declared is not None:
            return renderable_serializer_class(declared)
        if getattr(self, "serializer_class", None) is not None:
            return super().get_serializer_class()  # ty: ignore[unresolved-attribute]
        return _NoBody


def _declared_input(spec: ServiceSpec[Any, Any, Any] | PolymorphicServiceSpec) -> type | None:
    """The one ``input_serializer`` behind ``spec``, or ``None`` if there is none.

    A polymorphic spec has one only when every variant declares the same class;
    otherwise which body applies is the discriminator's call on a body OPTIONS
    does not have. The single-class condition is held by the
    ``polymorphic-distinct-inputs`` row of test_options_describes_every_spec_only_shape.
    """
    if isinstance(spec, ServiceSpec):
        return spec.input_serializer
    declared = {variant.input_serializer for variant in spec.specs.values()}
    return declared.pop() if len(declared) == 1 else None
