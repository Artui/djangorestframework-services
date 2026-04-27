"""Router-compatible viewsets, action mixins, and the ``@service_action`` decorator."""

from rest_framework_services.viewsets.decorators import service_action
from rest_framework_services.viewsets.multi_serializer_mixin import MultiSerializerMixin
from rest_framework_services.viewsets.selector_list_mixin import SelectorListMixin
from rest_framework_services.viewsets.selector_retrieve_mixin import (
    SelectorRetrieveMixin,
)
from rest_framework_services.viewsets.selector_viewset import SelectorViewSet
from rest_framework_services.viewsets.service_create_mixin import ServiceCreateMixin
from rest_framework_services.viewsets.service_destroy_mixin import ServiceDestroyMixin
from rest_framework_services.viewsets.service_update_mixin import ServiceUpdateMixin
from rest_framework_services.viewsets.service_viewset import ServiceViewSet

__all__ = [
    "MultiSerializerMixin",
    "SelectorListMixin",
    "SelectorRetrieveMixin",
    "SelectorViewSet",
    "ServiceCreateMixin",
    "ServiceDestroyMixin",
    "ServiceUpdateMixin",
    "ServiceViewSet",
    "service_action",
]
