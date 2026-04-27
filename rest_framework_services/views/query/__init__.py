"""Query views — ``List`` and ``Retrieve`` backed by selector callables."""

from rest_framework_services.views.query.selector_list_view import SelectorListView
from rest_framework_services.views.query.selector_retrieve_view import (
    SelectorRetrieveView,
)

__all__ = [
    "SelectorListView",
    "SelectorRetrieveView",
]
