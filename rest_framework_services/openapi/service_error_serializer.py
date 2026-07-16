"""Schema-only ``Serializer`` documenting the 422 ``ServiceError`` shape."""

from __future__ import annotations

from rest_framework import serializers


class ServiceErrorSerializer(serializers.Serializer):
    """Single-field ``detail`` serializer that mirrors ``_ServiceAPIException``.

    Used by :class:`ServiceAutoSchema` to attach a ``422`` response to every
    spec-driven mutation in the generated OpenAPI document. It is *only* used
    for schema generation; runtime 422 bodies are produced by DRF's exception
    handler from
    :class:`~rest_framework_services.views.mutation.map_service_error._ServiceAPIException`.
    """

    detail = serializers.CharField()
