"""Selector protocols — typed shapes for query callables."""

from rest_framework_services.selectors.async_selector import AsyncSelector
from rest_framework_services.selectors.list_selector import ListSelector
from rest_framework_services.selectors.output_selector import OutputSelector
from rest_framework_services.selectors.retrieve_selector import RetrieveSelector
from rest_framework_services.selectors.selector import Selector
from rest_framework_services.selectors.strict_list_selector import StrictListSelector
from rest_framework_services.selectors.strict_output_selector import StrictOutputSelector
from rest_framework_services.selectors.strict_retrieve_selector import (
    StrictRetrieveSelector,
)

__all__ = [
    "AsyncSelector",
    "ListSelector",
    "OutputSelector",
    "RetrieveSelector",
    "Selector",
    "StrictListSelector",
    "StrictOutputSelector",
    "StrictRetrieveSelector",
]
