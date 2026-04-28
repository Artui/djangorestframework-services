"""Read-only viewset composed of selector + action-serializer-resolver mixins."""

from __future__ import annotations

from rest_framework.viewsets import GenericViewSet

from rest_framework_services.viewsets.action_serializer_resolver import (
    ActionSerializerResolver,
)
from rest_framework_services.viewsets.selector_list_mixin import SelectorListMixin
from rest_framework_services.viewsets.selector_retrieve_mixin import (
    SelectorRetrieveMixin,
)


class SelectorViewSet(
    SelectorListMixin,
    SelectorRetrieveMixin,
    ActionSerializerResolver,
    GenericViewSet,
):
    """Read-only viewset for ``list`` + ``retrieve``.

    Composes :class:`SelectorListMixin`, :class:`SelectorRetrieveMixin`, and
    :class:`ActionSerializerResolver` over
    :class:`~rest_framework.viewsets.GenericViewSet`.
    """
