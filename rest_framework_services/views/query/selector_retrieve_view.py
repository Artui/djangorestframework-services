"""``GET`` detail endpoint backed by a selector callable or DRF lookup."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from django.core.exceptions import ObjectDoesNotExist
from rest_framework.exceptions import NotFound
from rest_framework.generics import GenericAPIView
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.selectors.utils import run_selector
from rest_framework_services.views.utils import (
    get_class_attr,
    resolve_callable_kwargs,
)


class SelectorRetrieveView(RetrieveModelMixin, GenericAPIView):
    """``GET`` endpoint that returns a single object.

    With ``selector`` set, the callable receives the URL kwargs (e.g. ``pk``)
    plus ``request``, ``user``, ``view``, and any extras returned from
    ``get_selector_kwargs()``. Returning ``None`` or raising
    ``Model.DoesNotExist`` results in a 404.

    With ``selector`` unset, the view falls back to ``self.get_object()`` —
    standard DRF lookup using ``queryset`` and ``lookup_field``.

    Set ``serializer_class`` to render the result, like any DRF retrieve view.
    """

    selector: ClassVar[Callable[..., Any] | None] = None

    def get_selector_kwargs(self) -> dict[str, Any]:
        return {}

    def get_object(self) -> Any:
        selector: Callable[..., Any] | None = get_class_attr(self, "selector")
        if selector is None:
            return super().get_object()
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

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self.retrieve(request, *args, **kwargs)
