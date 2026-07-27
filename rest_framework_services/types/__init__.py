"""Shared value types — used as inputs and outputs across the library.

These are intentionally framework-agnostic data carriers. They live outside
``mutations/`` so callers (current and future) that consume them as typed
inputs/outputs are not coupled to the mutation helpers themselves.
"""

from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.change_result import ChangeResult
from rest_framework_services.types.child_collection_change import ChildCollectionChange
from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.field_change import FieldChange
from rest_framework_services.types.http_extras import HttpExtras
from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)
from rest_framework_services.types.no_input import NoInput
from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.offline_service_view import OfflineServiceView
from rest_framework_services.types.polymorphic_service_spec import PolymorphicServiceSpec
from rest_framework_services.types.registered_spec import RegisteredSpec
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.service_view import ServiceView
from rest_framework_services.types.target_guard import TargetGuard
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.unset import UNSET, UnsetType

__all__ = [
    "DEFAULT_JSON_SCHEMA_REGISTRY",
    "UNSET",
    "ArgumentBinding",
    "ChangeResult",
    "ChildCollectionChange",
    "ChildSpec",
    "DispatchResult",
    "FieldChange",
    "HttpExtras",
    "JsonSchemaRegistry",
    "NoInput",
    "OfflineContext",
    "OfflineServiceView",
    "PolymorphicServiceSpec",
    "RegisteredSpec",
    "SelectorKind",
    "SelectorSpec",
    "ServiceSpec",
    "ServiceView",
    "TargetGuard",
    "UnknownArguments",
    "UnsetType",
]
