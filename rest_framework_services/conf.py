"""Settings access for djangorestframework-services.

Reads the ``REST_FRAMEWORK_SERVICES`` dict from Django settings and exposes
typed accessors with documented defaults. Importing this module does not
require Django settings to be configured — the accessors evaluate lazily.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

DEFAULTS: dict[str, Any] = {
    "ATOMIC_BY_DEFAULT": True,
}


def get_setting(name: str) -> Any:
    """Return a single setting, falling back to the default in ``DEFAULTS``."""
    user_settings: dict[str, Any] = getattr(settings, "REST_FRAMEWORK_SERVICES", {})
    if name in user_settings:
        return user_settings[name]
    return DEFAULTS[name]
