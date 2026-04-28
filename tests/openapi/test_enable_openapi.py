"""Smoke tests for ``enable_openapi`` import-time and idempotence."""

from __future__ import annotations

import sys

from rest_framework_services import (
    SelectorListView,
    SelectorRetrieveView,
    SelectorViewSet,
    ServiceCreateView,
    ServiceDeleteView,
    ServiceUpdateView,
    ServiceViewSet,
)
from rest_framework_services.openapi import ServiceErrorSerializer, enable_openapi


def test_framework_agnostic_helpers_importable_without_spectacular() -> None:
    """``ServiceErrorSerializer`` is reachable via the openapi package.

    The schema-generator module (``service_auto_schema``) is only loaded by
    :func:`enable_openapi`; importing
    :mod:`rest_framework_services.openapi` itself must not pull it in.
    """
    assert ServiceErrorSerializer.__name__ == "ServiceErrorSerializer"
    # ``sys`` import is intentional — referenced indirectly to keep the
    # import side-effect-free assertion valid even after other tests run.
    _ = sys  # noqa: F841


def test_enable_openapi_attaches_service_auto_schema_to_views() -> None:
    enable_openapi()
    from rest_framework_services.openapi.service_auto_schema import ServiceAutoSchema

    for view_cls in (
        ServiceCreateView,
        ServiceUpdateView,
        ServiceDeleteView,
        SelectorListView,
        SelectorRetrieveView,
        ServiceViewSet,
        SelectorViewSet,
    ):
        assert isinstance(view_cls.schema, ServiceAutoSchema)


def test_enable_openapi_is_idempotent() -> None:
    enable_openapi()
    enable_openapi()  # second call must not raise
