"""Generic service-layer error.

Framework-agnostic. Mapped to an HTTP response only at the view boundary.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Raised by services to signal a business-rule failure.

    Carries an optional structured ``detail`` payload so the view layer can
    surface a meaningful response without the service depending on DRF.
    """

    default_message: str = "Service error."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message if message is not None else self.default_message)
        self.message: str = message if message is not None else self.default_message
