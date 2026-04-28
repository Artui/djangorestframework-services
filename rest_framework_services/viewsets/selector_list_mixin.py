"""Action-keyed list selector override on top of ``ListModelMixin``."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework.mixins import ListModelMixin

from rest_framework_services.selectors.utils import run_selector
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.utils import resolve_callable_kwargs
from rest_framework_services.viewsets.utils import _ActionSpecsMixin


class SelectorListMixin(ListModelMixin, _ActionSpecsMixin):
    """Compose with :class:`~rest_framework.viewsets.GenericViewSet`.

    When ``action_specs["list"]`` is a :class:`SelectorSpec` with a
    non-``None`` ``selector``, ``get_queryset()`` invokes it instead of
    returning the configured ``queryset``. The rest of DRF's list flow —
    filter backends, pagination, serialization — is unchanged.

    ``action_specs["list"] = SelectorSpec(selector=None)`` or an absent
    ``"list"`` key both fall through to DRF's default ``get_queryset()``.
    Any other entry type raises :exc:`~django.core.exceptions.ImproperlyConfigured`.
    """

    # Provided at runtime by ``GenericViewSet`` / ``GenericAPIView``.
    request: Any
    kwargs: dict[str, Any]

    def get_selector_kwargs(self) -> dict[str, Any]:
        """Hook for additional kwargs available to the selector signature."""
        return {}

    def get_queryset(self) -> Any:
        entry: SelectorSpec | ServiceSpec | None = self.action_specs.get("list")
        if entry is None:
            return super().get_queryset()  # ty: ignore[unresolved-attribute]
        if not isinstance(entry, SelectorSpec):
            raise ImproperlyConfigured(
                f"action_specs['list'] must be a SelectorSpec, got "
                f"{type(entry).__name__}. "
                "Wrap the selector: SelectorSpec(selector=your_callable)."
            )
        if entry.selector is None:
            return super().get_queryset()  # ty: ignore[unresolved-attribute]
        pool: dict[str, Any] = {
            "request": self.request,
            "user": getattr(self.request, "user", None),
            "view": self,
            **self.kwargs,
            **self.get_selector_kwargs(),
        }
        return run_selector(entry.selector, resolve_callable_kwargs(entry.selector, pool))
