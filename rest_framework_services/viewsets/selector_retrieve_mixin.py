"""Action-keyed retrieve selector override on top of ``RetrieveModelMixin``."""

from __future__ import annotations

from typing import Any

from rest_framework.mixins import RetrieveModelMixin
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.selectors.utils import dispatch_selector_for_spec
from rest_framework_services.viewsets.utils import (
    _ActionSpecsMixin,
    resolve_action_selector_spec,
)


class SelectorRetrieveMixin(RetrieveModelMixin, _ActionSpecsMixin):
    """Compose with ``GenericViewSet``.

    When ``action_specs["retrieve"]`` is a
    [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] with
    a non-``None`` ``selector``, ``get_object()`` invokes it instead of
    falling through to DRF's standard lookup. The selector receives the
    URL kwargs plus the standard pool. Returning ``None`` or raising
    ``Model.DoesNotExist`` results in a 404 — or, when the spec sets
    ``allow_none=True``, a ``200`` with a JSON ``null`` body (the
    nullable-resource contract; the output serializer is skipped).

    ``action_specs["retrieve"] = SelectorSpec(selector=None)`` or an absent
    ``"retrieve"`` key both fall through to DRF's default ``get_object()``.
    Any other entry type raises
    ``ImproperlyConfigured``.

    The selector applies wherever ``get_object()`` is called, including from
    update/destroy actions composed alongside this mixin. If you need an
    action-specific override, do it explicitly in your own ``get_object()``.
    """

    # Provided at runtime by ``GenericViewSet`` / ``GenericAPIView``.
    request: Any
    kwargs: dict[str, Any]

    def get_selector_kwargs(self) -> dict[str, Any]:
        return {}

    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        spec = resolve_action_selector_spec(self.action_specs, "retrieve")
        if spec is None or not spec.allow_none:
            return super().retrieve(request, *args, **kwargs)
        # Nullable-resource contract: ``get_object()`` may resolve ``None``
        # (instead of raising ``NotFound``); render it as a literal JSON
        # ``null`` without invoking the output serializer.
        instance = self.get_object()
        if instance is None:
            return Response(None)
        serializer = self.get_serializer(instance)  # ty: ignore[unresolved-attribute]
        return Response(serializer.data)

    def get_object(self) -> Any:
        spec = resolve_action_selector_spec(self.action_specs, "retrieve")
        obj = (
            super().get_object()  # ty: ignore[unresolved-attribute]
            if spec is None
            else dispatch_selector_for_spec(self, spec)
        )
        # Stash the resolved instance so a SelectorSpec's output context
        # provider can read it via the ``instance`` extra. See
        # ``_ActionSpecsMixin.get_serializer_context``.
        self._resolved_instance = obj
        return obj
