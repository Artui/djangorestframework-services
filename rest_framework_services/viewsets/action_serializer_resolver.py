"""Per-action serializer dispatch for ViewSets driven by ``action_specs``."""

from __future__ import annotations

from typing import Any

from rest_framework.serializers import BaseSerializer

from rest_framework_services.dispatch.renderable_serializer_class import renderable_serializer_class
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

    OPTIONS is answered under DRF's implicit ``"metadata"`` action, once per
    POST / PUT it describes. There the entry is the one that method routes to,
    and a ``ServiceSpec`` answers with its ``input_serializer``: the body that
    method accepts, and the only serializer a no-content action declares.

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

    # Provided at runtime by ``GenericViewSet``.
    action: str | None
    action_map: dict[str, str]
    request: Any

    def get_serializer_class(self) -> type[BaseSerializer[Any]]:
        if self.action == "metadata":
            described = self._metadata_input_serializer()
            if described is not None:
                return described
        # Same ``"partial_update"`` → ``"update"`` fallback as dispatch and
        # ``get_permissions``, so the three sites agree.
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

    def _metadata_input_serializer(self) -> type[BaseSerializer[Any]] | None:
        # ``SimpleMetadata.determine_actions`` swaps in a clone of the request
        # carrying the method it describes, so ``request.method`` is that POST /
        # PUT while ``self.action`` stays ``"metadata"``.
        action = self.action_map.get(self.request.method.lower())
        spec = resolve_action_spec_entry(self.action_specs, action)
        if isinstance(spec, ServiceSpec):
            return renderable_serializer_class(spec.input_serializer)
        return None
