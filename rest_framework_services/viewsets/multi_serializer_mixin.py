"""Per-action serializer dispatch for ViewSets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from rest_framework.serializers import Serializer


class MultiSerializerMixin:
    """Map ``self.action`` to a serializer class.

    Set ``serializer_classes`` to a mapping from action name to serializer
    class. ``get_serializer_class()`` consults the map first, falling back to
    DRF's standard ``serializer_class`` (and raising the usual DRF error if
    neither is configured for the active action).

    Example::

        class InvoiceViewSet(MultiSerializerMixin, GenericViewSet):
            serializer_classes = {
                "list": InvoiceListSerializer,
                "retrieve": InvoiceDetailSerializer,
            }
    """

    # Provided at runtime by ``GenericViewSet``.
    action: str | None

    serializer_classes: ClassVar[Mapping[str, type[Serializer]]] = {}

    def get_serializer_class(self) -> type[Serializer]:
        action: str | None = self.action
        if action is not None:
            cls: type[Serializer] | None = self.serializer_classes.get(action)
            if cls is not None:
                return cls
        return super().get_serializer_class()  # ty: ignore[unresolved-attribute]
