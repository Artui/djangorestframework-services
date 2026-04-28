"""Action-keyed list selector override on top of ``ListModelMixin``."""

from __future__ import annotations

from typing import Any

from rest_framework.mixins import ListModelMixin

from rest_framework_services.selectors.utils import dispatch_selector_for_spec
from rest_framework_services.viewsets.utils import (
    _ActionSpecsMixin,
    resolve_action_selector_spec,
)


class SelectorListMixin(ListModelMixin, _ActionSpecsMixin):
    """Compose with :class:`~rest_framework.viewsets.GenericViewSet`.

    When ``action_specs["list"]`` is a
    :class:`~rest_framework_services.types.selector_spec.SelectorSpec` with
    a non-``None`` ``selector``, ``get_queryset()`` invokes it instead of
    returning the configured ``queryset``. The rest of DRF's list flow —
    filter backends, pagination, serialization — is unchanged.

    ``action_specs["list"] = SelectorSpec(selector=None)`` or an absent
    ``"list"`` key both fall through to DRF's default ``get_queryset()``.
    Any other entry type raises
    :exc:`~django.core.exceptions.ImproperlyConfigured`.
    """

    # Provided at runtime by ``GenericViewSet`` / ``GenericAPIView``.
    request: Any
    kwargs: dict[str, Any]

    def get_selector_kwargs(self) -> dict[str, Any]:
        """Hook for additional kwargs available to the selector signature."""
        return {}

    def get_queryset(self) -> Any:
        spec = resolve_action_selector_spec(self.action_specs, "list")
        if spec is None:
            return super().get_queryset()  # ty: ignore[unresolved-attribute]
        return dispatch_selector_for_spec(self, spec, extra_url_kwargs=self.kwargs)
