"""``@selector_action`` decorator — DRF ``@action`` plus selector plumbing."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.selectors.utils import (
    dispatch_retrieve_selector,
    dispatch_selector_for_spec,
)
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.views.spec_validation import validate_selector_spec


def selector_action(
    spec: SelectorSpec[Any, Any],
    *,
    detail: bool = False,
    methods: list[str] | None = None,
    url_path: str | None = None,
    url_name: str | None = None,
    **action_kwargs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Wrap a viewset method as a selector-backed custom action.

    The decorated method's body is *not* executed — the decorator supplies
    the handler. The method exists so that ``@selector_action`` can attach
    DRF ``@action`` metadata and pick up the action name from ``__name__``.

    Pass a :class:`SelectorSpec` for the selector wiring. ``detail``
    controls the dispatch shape:

    - ``detail=False`` (default) — collection action; the selector is
      expected to return an iterable. The result flows through
      ``self.paginate_queryset`` / ``self.get_paginated_response`` if
      pagination is configured, otherwise it's serialized many=True.
    - ``detail=True`` — detail action; the selector is expected to return a
      single object (or ``None``, which surfaces as 404, matching
      :class:`SelectorRetrieveView`).

    Output serialization resolves to ``spec.output_serializer`` when set,
    falling back to ``self.get_serializer(...)`` otherwise.
    """
    drf_kwargs: dict[str, Any] = {"detail": detail}
    if methods is not None:
        drf_kwargs["methods"] = methods
    if url_path is not None:
        drf_kwargs["url_path"] = url_path
    if url_name is not None:
        drf_kwargs["url_name"] = url_name
    drf_kwargs.update(action_kwargs)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn_label = getattr(fn, "__qualname__", repr(fn))
        validate_selector_spec(spec, label=f"@selector_action {fn_label}")

        @functools.wraps(fn)
        def handler(self: Any, request: Request, *args: Any, **kwargs: Any) -> Response:
            if detail:
                instance = dispatch_retrieve_selector(self, spec, extra_url_kwargs=self.kwargs)
                serializer = _build_serializer(self, spec, instance, many=False)
                return Response(serializer.data)

            result = dispatch_selector_for_spec(self, spec, extra_url_kwargs=self.kwargs)
            page = self.paginate_queryset(result) if hasattr(self, "paginate_queryset") else None
            if page is not None:
                serializer = _build_serializer(self, spec, page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = _build_serializer(self, spec, result, many=True)
            return Response(serializer.data)

        # Stash the spec on the handler so schema generators (and any future
        # introspection) can recover it; the closure is otherwise opaque.
        handler._selector_spec = spec  # ty: ignore[unresolved-attribute]
        return action(**drf_kwargs)(handler)

    return decorator


def _build_serializer(
    view: Any,
    spec: SelectorSpec[Any, Any],
    instance: Any,
    *,
    many: bool,
) -> Any:
    """Instantiate the response serializer for a ``@selector_action`` result.

    ``spec.output_serializer`` wins when set (matching the standalone
    selector views' override semantics); otherwise the viewset's
    ``get_serializer(...)`` is used so existing
    :class:`ActionSerializerResolver` wiring continues to apply.
    """
    if spec.output_serializer is not None:
        return spec.output_serializer(instance, many=many, context={"request": view.request})
    return view.get_serializer(instance, many=many)
