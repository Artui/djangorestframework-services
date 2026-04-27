"""Framework-agnostic exceptions raised by service code.

These classes have **no DRF imports**. The view layer is responsible for
translating them into HTTP responses.
"""

from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)

__all__ = [
    "ServiceError",
    "ServiceValidationError",
]
