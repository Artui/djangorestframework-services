"""``base_serializer_context`` lives in ``service_specs/``, which dispatch sits above.

It is the DRF serializer context, on or off HTTP, and the serializer adapter below ``dispatch/`` needs it, so the
definition moved down rather than letting ``service_specs/`` import ``dispatch/``.
"""

from __future__ import annotations

from rest_framework_services.service_specs.base_serializer_context import base_serializer_context

__all__ = ["base_serializer_context"]
