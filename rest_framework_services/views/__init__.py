"""Base view classes.

The mutation views (``ServiceCreateView``, ``ServiceUpdateView``,
``ServiceDeleteView``) and query views (``SelectorListView``,
``SelectorRetrieveView``) are re-exported here for convenience.
"""

from rest_framework_services.views.mutation import (
    ServiceCreateView,
    ServiceDeleteView,
    ServiceUpdateView,
)
from rest_framework_services.views.query import (
    SelectorListView,
    SelectorRetrieveView,
)

__all__ = [
    "SelectorListView",
    "SelectorRetrieveView",
    "ServiceCreateView",
    "ServiceDeleteView",
    "ServiceUpdateView",
]
