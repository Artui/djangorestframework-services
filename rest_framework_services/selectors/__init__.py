"""Selector protocols — typed shapes for query callables."""

from rest_framework_services.selectors.async_selector import AsyncSelector
from rest_framework_services.selectors.selector import Selector

__all__ = [
    "AsyncSelector",
    "Selector",
]
