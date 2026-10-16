"""``renderable_serializer_class`` lives in ``service_specs/``, which dispatch sits above.

It is the serializer class a spec renders under, and the serializer adapter below ``dispatch/`` needs it, so the
definition moved down rather than letting ``service_specs/`` import ``dispatch/``.
"""

from __future__ import annotations

from rest_framework_services.service_specs.renderable_serializer_class import (
    renderable_serializer_class,
)

__all__ = ["renderable_serializer_class"]
