"""Read-only viewset composed of selector + multi-serializer mixins."""

from __future__ import annotations

from rest_framework.viewsets import GenericViewSet

from rest_framework_services.viewsets.multi_serializer_mixin import MultiSerializerMixin
from rest_framework_services.viewsets.selector_list_mixin import SelectorListMixin
from rest_framework_services.viewsets.selector_retrieve_mixin import (
    SelectorRetrieveMixin,
)


class SelectorViewSet(
    SelectorListMixin,
    SelectorRetrieveMixin,
    MultiSerializerMixin,
    GenericViewSet,
):
    """Read-only viewset for ``list`` + ``retrieve``.

    Composes :class:`SelectorListMixin`, :class:`SelectorRetrieveMixin`, and
    :class:`MultiSerializerMixin` over
    :class:`~rest_framework.viewsets.GenericViewSet`.
    """
