"""Shared internal helpers for the ``openapi`` package."""

from __future__ import annotations

from typing import Any

from rest_framework import status as drf_status

# Default success status by action / view kind. Mirrors the runtime defaults
# in the standalone views and viewset mixins so the schema lines up with
# what the framework actually returns when ``ServiceSpec.success_status`` is
# unset.
_ACTION_DEFAULT_STATUS: dict[str, int] = {
    "create": drf_status.HTTP_201_CREATED,
    "update": drf_status.HTTP_200_OK,
    "partial_update": drf_status.HTTP_200_OK,
    "destroy": drf_status.HTTP_204_NO_CONTENT,
}
_VIEW_CLASS_DEFAULT_STATUS: dict[str, int] = {
    "ServiceCreateView": drf_status.HTTP_201_CREATED,
    "ServiceUpdateView": drf_status.HTTP_200_OK,
    "ServiceDeleteView": drf_status.HTTP_204_NO_CONTENT,
}


def default_status(view: Any) -> int:
    """Pick the HTTP status the runtime would emit when no override is set.

    Looks at ``view.action`` first (viewset path), then the view class name
    (standalone view path). Falls back to ``200 OK`` for ``@service_action``
    and any unrecognised surface, mirroring the decorator's own default.
    """
    action_name = getattr(view, "action", None)
    if isinstance(action_name, str) and action_name in _ACTION_DEFAULT_STATUS:
        return _ACTION_DEFAULT_STATUS[action_name]
    for cls in type(view).__mro__:
        status = _VIEW_CLASS_DEFAULT_STATUS.get(cls.__name__)
        if status is not None:
            return status
    return drf_status.HTTP_200_OK
