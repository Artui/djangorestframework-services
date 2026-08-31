"""Audience projection — one serializer, more than one kind of reader.

A serializer written for a frontend is read verbatim by a model when the same
spec is exposed as an agent tool, and the two want different subsets of it.
These helpers let the difference be declared once, on the field, and applied by
every agent transport identically. The DRF view path reads none of it.
"""

from rest_framework_services.audience.annotate_output_schema import annotate_output_schema
from rest_framework_services.audience.audience_projection_for_spec import (
    audience_projection_for_spec,
)
from rest_framework_services.audience.build_audience_projection import build_audience_projection
from rest_framework_services.audience.project_payload import project_payload

__all__ = [
    "audience_projection_for_spec",
    "annotate_output_schema",
    "build_audience_projection",
    "project_payload",
]
