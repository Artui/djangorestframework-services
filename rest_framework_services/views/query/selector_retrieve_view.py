"""``GET`` detail endpoint backed by a selector spec or DRF lookup."""

from __future__ import annotations

from typing import Any, ClassVar

from rest_framework.generics import GenericAPIView
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer

from rest_framework_services.selectors.utils import dispatch_retrieve_selector
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.views.spec_validation import validate_selector_view_spec
from rest_framework_services.views.utils import get_class_attr


class SelectorRetrieveView(RetrieveModelMixin, GenericAPIView):
    """``GET`` endpoint that returns a single object.

    Set ``spec`` to a :class:`SelectorSpec` to configure the selector and/or
    the output serializer. Both fields are optional:

    - ``spec.selector`` overrides ``get_object()``; ``None`` falls back to
      ``self.get_object()`` — standard DRF lookup using ``queryset`` and
      ``lookup_field``. Returning ``None`` or raising ``Model.DoesNotExist``
      results in a 404.
    - ``spec.output_serializer`` overrides ``get_serializer_class()``; ``None``
      falls back to DRF's standard ``serializer_class`` attribute.

    ``spec = None`` (the default) keeps both as vanilla DRF.
    """

    spec: ClassVar[SelectorSpec | None] = None

    @classmethod
    def as_view(cls, **initkwargs: Any) -> Any:
        validate_selector_view_spec(cls)
        return super().as_view(**initkwargs)

    def get_selector_kwargs(self) -> dict[str, Any]:
        return {}

    def get_serializer_class(self) -> type[BaseSerializer[Any]]:
        s: SelectorSpec | None = get_class_attr(self, "spec")
        if isinstance(s, SelectorSpec) and s.output_serializer is not None:
            return s.output_serializer
        return super().get_serializer_class()

    def get_object(self) -> Any:
        s: SelectorSpec | None = get_class_attr(self, "spec")
        if s is None or s.selector is None:
            return super().get_object()
        return dispatch_retrieve_selector(self, s, extra_url_kwargs=self.kwargs)

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return self.retrieve(request, *args, **kwargs)
