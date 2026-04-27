"""Adds an optional ``list_selector`` override on top of ``ListModelMixin``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from rest_framework.mixins import ListModelMixin

from rest_framework_services.selectors.utils import run_selector
from rest_framework_services.views.utils import (
    get_class_attr,
    resolve_callable_kwargs,
)


class SelectorListMixin(ListModelMixin):
    """Compose with :class:`~rest_framework.viewsets.GenericViewSet`.

    When ``list_selector`` is set, ``get_queryset()`` invokes it instead of
    returning the configured ``queryset``. The rest of DRF's list flow —
    filter backends, pagination, serialization — is unchanged.

    The selector is the canonical override for ``get_queryset()`` on this
    viewset. If you need an action-specific override, do it explicitly in
    your own ``get_queryset()``.
    """

    # Provided at runtime by ``GenericViewSet`` / ``GenericAPIView``.
    request: Any
    kwargs: dict[str, Any]

    list_selector: ClassVar[Callable[..., Any] | None] = None

    def get_selector_kwargs(self) -> dict[str, Any]:
        """Hook for additional kwargs available to the selector signature."""
        return {}

    def get_queryset(self) -> Any:
        selector: Callable[..., Any] | None = get_class_attr(self, "list_selector")
        if selector is None:
            return super().get_queryset()  # ty: ignore[unresolved-attribute]
        pool: dict[str, Any] = {
            "request": self.request,
            "user": getattr(self.request, "user", None),
            "view": self,
            **self.kwargs,
            **self.get_selector_kwargs(),
        }
        return run_selector(selector, resolve_callable_kwargs(selector, pool))
