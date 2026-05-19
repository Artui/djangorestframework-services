"""Selector protocols — typed shapes for query callables.

See :mod:`rest_framework_services.services` for the extras-typing
conventions; selectors follow the identical pattern.
"""

from rest_framework_services.selectors.acall_selector import acall_selector
from rest_framework_services.selectors.async_selector import AsyncSelector
from rest_framework_services.selectors.call_selector import call_selector
from rest_framework_services.selectors.list_selector import ListSelector
from rest_framework_services.selectors.output_selector import OutputSelector
from rest_framework_services.selectors.retrieve_selector import RetrieveSelector
from rest_framework_services.selectors.selector import Selector

__all__ = [
    "AsyncSelector",
    "ListSelector",
    "OutputSelector",
    "RetrieveSelector",
    "Selector",
    "acall_selector",
    "call_selector",
]
