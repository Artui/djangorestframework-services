"""Opt-in OpenAPI / Swagger integration for ``djangorestframework-services``.

The framework-agnostic helpers —
[`ServiceErrorSerializer`][rest_framework_services.openapi.service_error_serializer.ServiceErrorSerializer],
[`enable_openapi`][rest_framework_services.openapi.enable_openapi.enable_openapi] — live
here. Importing this module is safe even without ``drf-spectacular`` installed; it does
not pull in the schema-generator code itself.

The actual ``drf-spectacular`` integration lives in
``rest_framework_services.openapi.service_auto_schema``. That module
imports ``drf-spectacular`` at the top and is loaded only via
[`enable_openapi`][rest_framework_services.openapi.enable_openapi.enable_openapi].

Install the optional dependency with:

    pip install djangorestframework-services[spectacular]

Then call
[`enable_openapi`][rest_framework_services.openapi.enable_openapi.enable_openapi] once
from ``AppConfig.ready()``."""

from rest_framework_services.openapi.enable_openapi import enable_openapi
from rest_framework_services.openapi.service_error_serializer import (
    ServiceErrorSerializer,
)

__all__ = [
    "ServiceErrorSerializer",
    "enable_openapi",
]
