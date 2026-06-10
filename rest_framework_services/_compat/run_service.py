"""Backwards-compatibility shim — ``run_service`` moved to ``services/`` in 0.17.

Kept because downstream packages (drf-mcp ≤0.7) import this leaf path.
New code should import from ``rest_framework_services`` (top level) or
``rest_framework_services.services.run_service``.
"""

from rest_framework_services.services.run_service import run_service

__all__ = ["run_service"]
