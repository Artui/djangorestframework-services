"""Shared value types — used as inputs and outputs across the library.

These are intentionally framework-agnostic data carriers. They live outside
``mutations/`` so callers (current and future) that consume them as typed
inputs/outputs are not coupled to the mutation helpers themselves.
"""

from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.audience_projection import AudienceProjection
from rest_framework_services.types.change_result import ChangeResult
from rest_framework_services.types.child_collection_change import ChildCollectionChange
from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.field_audience import FieldAudience
from rest_framework_services.types.field_change import FieldChange
from rest_framework_services.types.field_marking import MARKING, FieldMarking
from rest_framework_services.types.forward_relation_spec import ForwardRelationSpec
from rest_framework_services.types.generic_relation_spec import GenericRelationSpec
from rest_framework_services.types.http_extras import HttpExtras
from rest_framework_services.types.input_required import InputRequired, InputRequiredType
from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)
from rest_framework_services.types.many_to_many_spec import ManyToManySpec
from rest_framework_services.types.no_input import NoInput
from rest_framework_services.types.not_client_input import NotClientInput, NotClientInputType
from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.offline_contract import OfflineContract
from rest_framework_services.types.offline_http_request import OfflineHttpRequest
from rest_framework_services.types.offline_service_view import OfflineServiceView
from rest_framework_services.types.output_page import OutputPage
from rest_framework_services.types.polymorphic_service_spec import PolymorphicServiceSpec
from rest_framework_services.types.progress_reporter import ProgressReporter
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.read_schema_markers import read_schema_markers
from rest_framework_services.types.registered_spec import RegisteredSpec
from rest_framework_services.types.related_object_change import RelatedObjectChange
from rest_framework_services.types.relation_mode import RelationMode
from rest_framework_services.types.relation_orphan import RelationOrphan
from rest_framework_services.types.relation_outcome import RelationOutcome
from rest_framework_services.types.relation_phase import RelationPhase
from rest_framework_services.types.relation_spec import RelationSpec
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.reverse_one_to_one_spec import ReverseOneToOneSpec
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.service_view import ServiceView
from rest_framework_services.types.target_guard import TargetGuard
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.unset import UNSET, UnsetType
from rest_framework_services.types.url_kwarg import UrlKwarg
from rest_framework_services.types.validate_channel_names import validate_channel_names
from rest_framework_services.types.view_hooks import ViewHooks

__all__ = [
    "MARKING",
    "FieldMarking",
    "OutputPage",
    "AudienceProjection",
    "DEFAULT_JSON_SCHEMA_REGISTRY",
    "RESERVED_POOL_SEEDS",
    "UNSET",
    "ArgumentBinding",
    "ChangeResult",
    "ChildCollectionChange",
    "ChildSpec",
    "DispatchResult",
    "FieldChange",
    "ForwardRelationSpec",
    "GenericRelationSpec",
    "HttpExtras",
    "InputRequired",
    "InputRequiredType",
    "JsonSchemaRegistry",
    "ManyToManySpec",
    "NoInput",
    "NotClientInput",
    "NotClientInputType",
    "OfflineContext",
    "OfflineHttpRequest",
    "OfflineServiceView",
    "PolymorphicServiceSpec",
    "ProgressReporter",
    "QueryParam",
    "OfflineContract",
    "RegisteredSpec",
    "RelatedObjectChange",
    "RelationMode",
    "RelationOrphan",
    "RelationOutcome",
    "RelationPhase",
    "RelationSpec",
    "ReverseOneToOneSpec",
    "FieldAudience",
    "SelectorKind",
    "SelectorSpec",
    "ServiceSpec",
    "ServiceView",
    "TargetGuard",
    "ViewHooks",
    "UnknownArguments",
    "ViewHooks",
    "UnsetType",
    "UrlKwarg",
    "read_schema_markers",
    "validate_channel_names",
]
