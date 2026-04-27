"""Adds an optional ``retrieve_selector`` override on top of ``RetrieveModelMixin``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from django.core.exceptions import ObjectDoesNotExist
from rest_framework.exceptions import NotFound
from rest_framework.mixins import RetrieveModelMixin

from rest_framework_services.selectors.utils import run_selector
from rest_framework_services.views.utils import (
    get_class_attr,
    resolve_callable_kwargs,
)


class SelectorRetrieveMixin(RetrieveModelMixin):
    """Compose with :class:`~rest_framework.viewsets.GenericViewSet`.

    When ``retrieve_selector`` is set, ``get_object()`` invokes it instead
    of falling through to DRF's standard lookup. The selector receives the
    URL kwargs plus the standard pool. Returning ``None`` or raising
    ``Model.DoesNotExist`` results in a 404.

    The selector is the canonical override for ``get_object()`` on this
    viewset — it applies wherever ``get_object()`` is called, including
    from update/destroy actions composed alongside this mixin. If you need
    an action-specific override, do it explicitly in your own
    ``get_object()``.

    When ``retrieve_selector`` is unset, ``get_object()`` falls back to
    DRF's default lookup using ``queryset`` and ``lookup_field``.
    """

    # Provided at runtime by ``GenericViewSet`` / ``GenericAPIView``.
    request: Any
    kwargs: dict[str, Any]

    retrieve_selector: ClassVar[Callable[..., Any] | None] = None

    def get_selector_kwargs(self) -> dict[str, Any]:
        return {}

    def get_object(self) -> Any:
        selector: Callable[..., Any] | None = get_class_attr(self, "retrieve_selector")
        if selector is None:
            return super().get_object()  # ty: ignore[unresolved-attribute]
        pool: dict[str, Any] = {
            "request": self.request,
            "user": getattr(self.request, "user", None),
            "view": self,
            **self.kwargs,
            **self.get_selector_kwargs(),
        }
        try:
            instance: Any = run_selector(selector, resolve_callable_kwargs(selector, pool))
        except ObjectDoesNotExist as exc:
            raise NotFound() from exc
        if instance is None:
            raise NotFound()
        return instance
