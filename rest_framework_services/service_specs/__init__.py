"""The DRF adapter over ``django-service-specs``.

This subpackage imports DRF by design: it is where a serializer becomes a
validator and a presenter, DRF's permission classes become a permission check,
and a spec declared here compiles to the kernel's.
"""

from __future__ import annotations
