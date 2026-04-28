"""Per-action serializer dispatch for ViewSets driven by ``action_specs``."""

from __future__ import annotations

from rest_framework.serializers import Serializer

from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.viewsets.utils import _ActionSpecsMixin


class ActionSerializerResolver(_ActionSpecsMixin):
    """Resolve ``get_serializer_class()`` from ``action_specs``.

    Consults the ``action_specs`` map for an ``output_serializer`` on the
    active action's :class:`SelectorSpec` or :class:`ServiceSpec` entry,
    then falls back to DRF's standard ``serializer_class`` attribute (and
    raises the usual DRF ``AssertionError`` if neither is set).

    Example::

        class InvoiceViewSet(ActionSerializerResolver, GenericViewSet):
            action_specs = {
                "list": SelectorSpec(output_serializer=InvoiceListSerializer),
                "retrieve": SelectorSpec(output_serializer=InvoiceDetailSerializer),
            }
    """

    # Provided at runtime by ``GenericViewSet``.
    action: str | None

    def get_serializer_class(self) -> type[Serializer]:
        spec = self.action_specs.get(self.action) if self.action else None
        if isinstance(spec, (SelectorSpec, ServiceSpec)) and spec.output_serializer is not None:
            return spec.output_serializer
        return super().get_serializer_class()  # ty: ignore[unresolved-attribute]
