"""Action-keyed retrieve selector override on top of ``RetrieveModelMixin``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from django.core.exceptions import ObjectDoesNotExist
from rest_framework.exceptions import NotFound
from rest_framework.mixins import RetrieveModelMixin

from rest_framework_services.selectors.utils import run_selector
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.utils import resolve_callable_kwargs


class SelectorRetrieveMixin(RetrieveModelMixin):
    """Compose with :class:`~rest_framework.viewsets.GenericViewSet`.

    When ``service_specs["retrieve"]`` is set to a callable, ``get_object()``
    invokes it instead of falling through to DRF's standard lookup. The
    selector receives the URL kwargs plus the standard pool. Returning
    ``None`` or raising ``Model.DoesNotExist`` results in a 404.

    The callable is the canonical override for ``get_object()`` on this
    viewset — it applies wherever ``get_object()`` is called, including
    from update/destroy actions composed alongside this mixin. If you need
    an action-specific override, do it explicitly in your own
    ``get_object()``.

    When the ``"retrieve"`` key is unset, ``get_object()`` falls back to
    DRF's default lookup using ``queryset`` and ``lookup_field``.
    """

    # Provided at runtime by ``GenericViewSet`` / ``GenericAPIView``.
    request: Any
    kwargs: dict[str, Any]

    service_specs: ClassVar[Mapping[str, Callable[..., Any] | ServiceSpec]] = {}

    def get_selector_kwargs(self) -> dict[str, Any]:
        return {}

    def get_object(self) -> Any:
        entry: Callable[..., Any] | ServiceSpec | None = self.service_specs.get("retrieve")
        if not callable(entry):
            return super().get_object()  # ty: ignore[unresolved-attribute]
        pool: dict[str, Any] = {
            "request": self.request,
            "user": getattr(self.request, "user", None),
            "view": self,
            **self.kwargs,
            **self.get_selector_kwargs(),
        }
        try:
            instance: Any = run_selector(entry, resolve_callable_kwargs(entry, pool))
        except ObjectDoesNotExist as exc:
            raise NotFound() from exc
        if instance is None:
            raise NotFound()
        return instance
