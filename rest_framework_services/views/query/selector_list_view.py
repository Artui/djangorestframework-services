"""``GET`` list endpoint backed by a selector callable or DRF queryset."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from rest_framework.generics import GenericAPIView
from rest_framework.mixins import ListModelMixin
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.selectors.utils import run_selector
from rest_framework_services.views.utils import (
    get_class_attr,
    resolve_callable_kwargs,
)


class SelectorListView(ListModelMixin, GenericAPIView):
    """``GET`` endpoint that delegates to a selector or to ``get_queryset()``.

    The ``selector`` attribute is a callable returning a queryset/list. When
    set, it overrides ``get_queryset()``; the rest of the flow (filter
    backends, pagination, response rendering) is the standard DRF
    ``ListModelMixin``. When ``selector`` is unset, the view uses the
    inherited ``queryset`` attribute exactly like a vanilla DRF list view.

    Set ``serializer_class`` to render items, like any DRF list view.
    """

    selector: ClassVar[Callable[..., Any] | None] = None

    def get_selector_kwargs(self) -> dict[str, Any]:
        """Hook for additional kwargs available to the selector signature."""
        return {}

    def get_queryset(self) -> Any:
        selector: Callable[..., Any] | None = get_class_attr(self, "selector")
        if selector is None:
            return super().get_queryset()
        pool: dict[str, Any] = {
            "request": self.request,
            "user": getattr(self.request, "user", None),
            "view": self,
            **self.kwargs,
            **self.get_selector_kwargs(),
        }
        return run_selector(selector, resolve_callable_kwargs(selector, pool))

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self.list(request, *args, **kwargs)
