"""JSON Schema generation for specs — the view-free schema surface.

These helpers turn a ``ServiceSpec`` / ``SelectorSpec`` (or a bare DRF
serializer / dataclass) into a JSON Schema dict, with **no** view, request, or
``drf-spectacular`` dependency. They are what an alternate transport (a
Pydantic-AI toolset, the MCP server) builds tool definitions from. Distinct
from :mod:`rest_framework_services.openapi`, which produces DRF serializer
classes for DRF's own OpenAPI generators.
"""

from rest_framework_services.jsonschema.filterset_to_json_schema import filterset_to_json_schema
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.jsonschema.serializer_to_json_schema import serializer_to_json_schema
from rest_framework_services.jsonschema.spec_to_json_schema import spec_to_json_schema

__all__ = [
    "filterset_to_json_schema",
    "output_to_json_schema",
    "serializer_to_json_schema",
    "spec_to_json_schema",
]
