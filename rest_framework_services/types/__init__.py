"""Shared value types — used as inputs and outputs across the library.

These are intentionally framework-agnostic data carriers. They live outside
``mutations/`` so callers (current and future) that consume them as typed
inputs/outputs are not coupled to the mutation helpers themselves.
"""

from rest_framework_services.types.change_result import ChangeResult
from rest_framework_services.types.field_change import FieldChange
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.service_view import ServiceView
from rest_framework_services.types.unset import UNSET

__all__ = [
    "UNSET",
    "ChangeResult",
    "FieldChange",
    "SelectorSpec",
    "ServiceSpec",
    "ServiceView",
]
