"""Opt-in helper that wires [`ServiceAutoSchema`][rest_framework_services.openapi.service_auto_schema.ServiceAutoSchema] onto every library view."""

from __future__ import annotations


def enable_openapi() -> None:
    """Set [`ServiceAutoSchema`][rest_framework_services.openapi.service_auto_schema.ServiceAutoSchema] as the schema on every library view class.

    Call once from ``AppConfig.ready()`` (or anywhere after Django apps are
    loaded) to make ``drf-spectacular`` see ``ServiceSpec`` request bodies,
    response bodies, success statuses, and 422 contracts on:

    - ``ServiceCreateView``, ``ServiceUpdateView``, ``ServiceDeleteView``
    - ``SelectorListView``, ``SelectorRetrieveView``
    - ``ServiceViewSet``, ``SelectorViewSet``

    The function-local imports keep ``drf-spectacular`` an optional
    dependency: importing
    ``rest_framework_services.openapi`` does not require it, and only
    this call brings in the schema-generator code.

    User subclasses inherit ``schema = ServiceAutoSchema()`` automatically;
    classes that set ``schema = ...`` explicitly keep their own choice.
    """
    # Local imports per CLAUDE.md "Lazy / function-local imports are
    # forbidden unless... a proven optional dependency": ``drf-spectacular``
    # is exactly that.
    from rest_framework_services.openapi.service_auto_schema import ServiceAutoSchema
    from rest_framework_services.views.mutation.service_create_view import (
        ServiceCreateView,
    )
    from rest_framework_services.views.mutation.service_delete_view import (
        ServiceDeleteView,
    )
    from rest_framework_services.views.mutation.service_update_view import (
        ServiceUpdateView,
    )
    from rest_framework_services.views.query.selector_list_view import SelectorListView
    from rest_framework_services.views.query.selector_retrieve_view import (
        SelectorRetrieveView,
    )
    from rest_framework_services.viewsets.selector_viewset import SelectorViewSet
    from rest_framework_services.viewsets.service_viewset import ServiceViewSet

    for view_cls in (
        ServiceCreateView,
        ServiceUpdateView,
        ServiceDeleteView,
        SelectorListView,
        SelectorRetrieveView,
        ServiceViewSet,
        SelectorViewSet,
    ):
        view_cls.schema = ServiceAutoSchema()
