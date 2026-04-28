"""Action-keyed retrieve selector override on top of ``RetrieveModelMixin``."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from rest_framework.exceptions import NotFound
from rest_framework.mixins import RetrieveModelMixin

from rest_framework_services.selectors.utils import run_selector
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.utils import resolve_callable_kwargs
from rest_framework_services.viewsets.utils import _ActionSpecsMixin


class SelectorRetrieveMixin(RetrieveModelMixin, _ActionSpecsMixin):
    """Compose with :class:`~rest_framework.viewsets.GenericViewSet`.

    When ``action_specs["retrieve"]`` is a :class:`SelectorSpec` with a
    non-``None`` ``selector``, ``get_object()`` invokes it instead of
    falling through to DRF's standard lookup. The selector receives the URL
    kwargs plus the standard pool. Returning ``None`` or raising
    ``Model.DoesNotExist`` results in a 404.

    ``action_specs["retrieve"] = SelectorSpec(selector=None)`` or an absent
    ``"retrieve"`` key both fall through to DRF's default ``get_object()``.
    Any other entry type raises :exc:`~django.core.exceptions.ImproperlyConfigured`.

    The selector applies wherever ``get_object()`` is called, including from
    update/destroy actions composed alongside this mixin. If you need an
    action-specific override, do it explicitly in your own ``get_object()``.
    """

    # Provided at runtime by ``GenericViewSet`` / ``GenericAPIView``.
    request: Any
    kwargs: dict[str, Any]

    def get_selector_kwargs(self) -> dict[str, Any]:
        return {}

    def get_object(self) -> Any:
        entry: SelectorSpec | ServiceSpec | None = self.action_specs.get("retrieve")
        if entry is None:
            return super().get_object()  # ty: ignore[unresolved-attribute]
        if not isinstance(entry, SelectorSpec):
            raise ImproperlyConfigured(
                f"action_specs['retrieve'] must be a SelectorSpec, got "
                f"{type(entry).__name__}. "
                "Wrap the selector: SelectorSpec(selector=your_callable)."
            )
        if entry.selector is None:
            return super().get_object()  # ty: ignore[unresolved-attribute]
        pool: dict[str, Any] = {
            "request": self.request,
            "user": getattr(self.request, "user", None),
            "view": self,
            **self.kwargs,
            **self.get_selector_kwargs(),
        }
        try:
            instance: Any = run_selector(
                entry.selector, resolve_callable_kwargs(entry.selector, pool)
            )
        except ObjectDoesNotExist as exc:
            raise NotFound() from exc
        if instance is None:
            raise NotFound()
        return instance
