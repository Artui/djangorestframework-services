"""djangorestframework-services — service/selector layer for Django REST Framework.

Public API re-exports live here. Importing the top-level package is enough
for typical usage; deeper imports (``rest_framework_services.mutations``,
``rest_framework_services.exceptions``, ``rest_framework_services.types``,
``rest_framework_services.views``, ``rest_framework_services.viewsets``,
``rest_framework_services.selectors``) are stable and supported.
"""

from rest_framework_services.exceptions import ServiceError, ServiceValidationError
from rest_framework_services.mutations import (
    acreate_from_input,
    apply_input,
    aupdate_from_input,
    create_from_input,
    update_from_input,
)
from rest_framework_services.selectors import AsyncSelector, Selector
from rest_framework_services.types import (
    UNSET,
    ChangeResult,
    FieldChange,
    SelectorSpec,
    ServiceSpec,
)
from rest_framework_services.views import (
    SelectorListView,
    SelectorRetrieveView,
    ServiceCreateView,
    ServiceDeleteView,
    ServiceUpdateView,
)
from rest_framework_services.views.mutation.mutation_flow_mixin import MutationFlowMixin
from rest_framework_services.viewsets import (
    ActionSerializerResolver,
    SelectorListMixin,
    SelectorRetrieveMixin,
    SelectorViewSet,
    ServiceCreateMixin,
    ServiceDestroyMixin,
    ServiceUpdateMixin,
    ServiceViewSet,
    service_action,
)

__all__ = [
    "ActionSerializerResolver",
    "AsyncSelector",
    "ChangeResult",
    "FieldChange",
    "MutationFlowMixin",
    "Selector",
    "SelectorListMixin",
    "SelectorListView",
    "SelectorRetrieveMixin",
    "SelectorRetrieveView",
    "SelectorSpec",
    "SelectorViewSet",
    "ServiceCreateMixin",
    "ServiceCreateView",
    "ServiceDeleteView",
    "ServiceDestroyMixin",
    "ServiceError",
    "ServiceSpec",
    "ServiceUpdateMixin",
    "ServiceUpdateView",
    "ServiceValidationError",
    "ServiceViewSet",
    "UNSET",
    "acreate_from_input",
    "apply_input",
    "aupdate_from_input",
    "create_from_input",
    "service_action",
    "update_from_input",
]

__version__ = "0.5.0"
