"""Mutation views — ``Create``, ``Update``, ``Delete`` backed by services."""

from rest_framework_services.views.mutation.service_create_view import ServiceCreateView
from rest_framework_services.views.mutation.service_delete_view import ServiceDeleteView
from rest_framework_services.views.mutation.service_update_view import ServiceUpdateView

__all__ = [
    "ServiceCreateView",
    "ServiceDeleteView",
    "ServiceUpdateView",
]
