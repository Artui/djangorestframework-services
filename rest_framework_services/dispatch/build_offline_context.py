"""``build_offline_context`` lives in ``service_specs/``, which dispatch sits above.

It is the offline request and view a call without HTTP runs under, and the serializer adapter below ``dispatch/`` needs it, so the
definition moved down rather than letting ``service_specs/`` import ``dispatch/``.
"""

from __future__ import annotations

from rest_framework_services.service_specs.build_offline_context import build_offline_context

__all__ = ["build_offline_context"]
