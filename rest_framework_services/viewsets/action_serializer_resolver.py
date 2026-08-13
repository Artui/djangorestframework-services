"""Per-action serializer dispatch for ViewSets driven by ``action_specs``."""

from __future__ import annotations

from rest_framework.serializers import Serializer

from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.viewsets.utils import (
    _ActionSpecsMixin,
    resolve_action_spec_entry,
)


class ActionSerializerResolver(_ActionSpecsMixin):
    """Resolve ``get_serializer_class()`` from ``action_specs``.

    Reads the active action's entry: a
    [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] supplies
    ``output_serializer``, a
    [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] supplies
    ``output_selector_spec.output_serializer``. Falls back to DRF's
    ``serializer_class``, raising the usual ``AssertionError`` when neither is set.

    Example::

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

    # Provided at runtime by ``GenericViewSet``.
    action: str | None

    def get_serializer_class(self) -> type[Serializer]:
        # Same ``"partial_update"`` → ``"update"`` fallback as dispatch and
        # ``get_permissions``, so the three sites agree.
        spec = resolve_action_spec_entry(self.action_specs, self.action)
        if isinstance(spec, SelectorSpec) and spec.output_serializer is not None:
            return spec.output_serializer
        if (
            isinstance(spec, ServiceSpec)
            and spec.output_selector_spec is not None
            and spec.output_selector_spec.output_serializer is not None
        ):
            return spec.output_selector_spec.output_serializer
        return super().get_serializer_class()  # ty: ignore[unresolved-attribute]
