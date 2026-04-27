"""Internal sync/async dispatch + atomic-transaction wrapping primitives."""

from rest_framework_services._compat.arun_service import arun_service
from rest_framework_services._compat.is_async import is_async
from rest_framework_services._compat.run_service import run_service

__all__ = [
    "arun_service",
    "is_async",
    "run_service",
]
