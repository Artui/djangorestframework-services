"""Full-CRUD viewset composed of per-action mixins."""

from __future__ import annotations

from rest_framework.viewsets import GenericViewSet

from rest_framework_services.viewsets.action_serializer_resolver import (
    ActionSerializerResolver,
)
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
    ActionSerializerResolver,
    GenericViewSet,
):
    """Router-compatible viewset wiring services and selectors.

    Composes
    [`ServiceCreateMixin`][rest_framework_services.viewsets.service_create_mixin.ServiceCreateMixin],
    [`ServiceUpdateMixin`][rest_framework_services.viewsets.service_update_mixin.ServiceUpdateMixin],
    [`ServiceDestroyMixin`][rest_framework_services.viewsets.service_destroy_mixin.ServiceDestroyMixin],
    [`SelectorListMixin`][rest_framework_services.viewsets.selector_list_mixin.SelectorListMixin],
    [`SelectorRetrieveMixin`][rest_framework_services.viewsets.selector_retrieve_mixin.SelectorRetrieveMixin],
    and
    [`ActionSerializerResolver`][rest_framework_services.viewsets.action_serializer_resolver.ActionSerializerResolver]
    over ``GenericViewSet``. See those classes for the configurable attributes."""
