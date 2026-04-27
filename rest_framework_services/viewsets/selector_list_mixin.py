"""Action-keyed list selector override on top of ``ListModelMixin``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from rest_framework.mixins import ListModelMixin

from rest_framework_services.selectors.utils import run_selector
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.utils import resolve_callable_kwargs


class SelectorListMixin(ListModelMixin):
    """Compose with :class:`~rest_framework.viewsets.GenericViewSet`.

    When ``service_specs["list"]`` is set to a callable, ``get_queryset()``
    invokes it instead of returning the configured ``queryset``. The rest
    of DRF's list flow — filter backends, pagination, serialization — is
    unchanged.

    The callable is the canonical override for ``get_queryset()`` on this
    viewset. If you need an action-specific override, do it explicitly in
    your own ``get_queryset()``.
    """

    # Provided at runtime by ``GenericViewSet`` / ``GenericAPIView``.
    request: Any
    kwargs: dict[str, Any]

    service_specs: ClassVar[Mapping[str, Callable[..., Any] | ServiceSpec]] = {}

    def get_selector_kwargs(self) -> dict[str, Any]:
        """Hook for additional kwargs available to the selector signature."""
        return {}

    def get_queryset(self) -> Any:
        entry: Callable[..., Any] | ServiceSpec | None = self.service_specs.get("list")
        if not callable(entry):
            return super().get_queryset()  # ty: ignore[unresolved-attribute]
        pool: dict[str, Any] = {
            "request": self.request,
            "user": getattr(self.request, "user", None),
            "view": self,
            **self.kwargs,
            **self.get_selector_kwargs(),
        }
        return run_selector(entry, resolve_callable_kwargs(entry, pool))
