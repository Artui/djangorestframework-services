"""Full-CRUD viewset composed of per-action mixins."""

from __future__ import annotations

from rest_framework.viewsets import GenericViewSet

from rest_framework_services.viewsets.multi_serializer_mixin import MultiSerializerMixin
from rest_framework_services.viewsets.selector_list_mixin import SelectorListMixin
from rest_framework_services.viewsets.selector_retrieve_mixin import (
    SelectorRetrieveMixin,
)
from rest_framework_services.viewsets.service_create_mixin import ServiceCreateMixin
from rest_framework_services.viewsets.service_destroy_mixin import ServiceDestroyMixin
from rest_framework_services.viewsets.service_update_mixin import ServiceUpdateMixin


class ServiceViewSet(
    ServiceCreateMixin,
    ServiceUpdateMixin,
    ServiceDestroyMixin,
    SelectorListMixin,
    SelectorRetrieveMixin,
    MultiSerializerMixin,
    GenericViewSet,
):
    """Router-compatible viewset wiring services and selectors.

    Composes :class:`ServiceCreateMixin`, :class:`ServiceUpdateMixin`,
    :class:`ServiceDestroyMixin`, :class:`SelectorListMixin`,
    :class:`SelectorRetrieveMixin`, and :class:`MultiSerializerMixin` over
    :class:`~rest_framework.viewsets.GenericViewSet`. See those classes for
    the configurable attributes.
    """
