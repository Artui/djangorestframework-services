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

    Consults the ``action_specs`` map for the active action's entry:

    - :class:`SelectorSpec` — uses ``spec.output_serializer`` directly.
    - :class:`ServiceSpec` — uses
      ``spec.output_selector_spec.output_serializer`` (the output pipeline
      is collapsed into the nested selector spec).

    Falls back to DRF's standard ``serializer_class`` attribute (and raises
    the usual DRF ``AssertionError`` if neither is set).

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
        # Same fallback chain as dispatch and ``get_permissions``
        # (``"partial_update"`` → ``"update"``) so the three sites agree.
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
