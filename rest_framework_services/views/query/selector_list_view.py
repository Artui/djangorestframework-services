"""``GET`` list endpoint backed by a selector spec or DRF queryset."""

from __future__ import annotations

from typing import Any, ClassVar

from rest_framework.generics import GenericAPIView
from rest_framework.mixins import ListModelMixin
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer

from rest_framework_services.selectors.utils import dispatch_selector_for_spec
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.views.spec_validation import validate_selector_view_spec
from rest_framework_services.views.utils import get_class_attr


class SelectorListView(ListModelMixin, GenericAPIView):
    """``GET`` endpoint that delegates to a selector or to ``get_queryset()``.

    Set ``spec`` to a :class:`SelectorSpec` to configure the selector and/or
    the output serializer. Both fields are optional:

    - ``spec.selector`` overrides ``get_queryset()``; ``None`` falls back to
      the inherited ``queryset`` attribute.
    - ``spec.output_serializer`` overrides ``get_serializer_class()``; ``None``
      falls back to DRF's standard ``serializer_class`` attribute.

    ``spec = None`` (the default) keeps both as vanilla DRF.

    The rest of the list flow — filter backends, pagination, response
    rendering — is the standard DRF ``ListModelMixin``.
    """

    spec: ClassVar[SelectorSpec | None] = None

    @classmethod
    def as_view(cls, **initkwargs: Any) -> Any:
        validate_selector_view_spec(cls)
        return super().as_view(**initkwargs)

    def get_selector_kwargs(self) -> dict[str, Any]:
        """Hook for additional kwargs available to the selector signature."""
        return {}

    def get_serializer_class(self) -> type[BaseSerializer[Any]]:
        s: SelectorSpec | None = get_class_attr(self, "spec")
        if isinstance(s, SelectorSpec) and s.output_serializer is not None:
            return s.output_serializer
        return super().get_serializer_class()

    def get_queryset(self) -> Any:
        s: SelectorSpec | None = get_class_attr(self, "spec")
        if s is None or s.selector is None:
            return super().get_queryset()
        return dispatch_selector_for_spec(self, s, extra_url_kwargs=self.kwargs)

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self.list(request, *args, **kwargs)
