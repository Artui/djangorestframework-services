"""``@selector_action`` decorator — DRF ``@action`` plus selector plumbing."""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from typing import Any

from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework_services.selectors.utils import dispatch_selector_for_spec
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.views.spec_validation import validate_selector_spec
from rest_framework_services.views.utils import resolve_serializer_context


def selector_action(
    spec: SelectorSpec[Any, Any],
    *,
    methods: list[str] | None = None,
    url_path: str | None = None,
    url_name: str | None = None,
    **action_kwargs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Wrap a viewset method as a selector-backed custom action.

    The decorated method's body is *not* executed — the decorator supplies
    the handler. The method exists so that ``@selector_action`` can attach
    DRF ``@action`` metadata and pick up the action name from ``__name__``.

    Pass a :class:`SelectorSpec` for the selector wiring. The dispatch shape
    and the DRF URL shape are both driven by ``spec.kind`` — ``kind`` is
    the single source of truth, with no separate ``detail=`` parameter to
    keep in sync:

    - :attr:`SelectorKind.LIST` — collection action (``detail=False``); the
      selector is expected to return an iterable. The result flows through
      ``self.paginate_queryset`` / ``self.get_paginated_response`` if
      pagination is configured, otherwise it's serialized many=True.
    - :attr:`SelectorKind.RETRIEVE` — detail action (``detail=True``); the
      selector is expected to return a single object (or ``None`` / raise
      :exc:`~django.core.exceptions.ObjectDoesNotExist`, both of which
      surface as 404).

    If you need a URL shape that doesn't match the response shape (a
    detail action that returns a list, or a collection action that returns
    a single resource), fall back to DRF's plain ``@action`` and write
    the dispatch yourself.

    Output serialization resolves to ``spec.output_serializer`` when set,
    falling back to ``self.get_serializer(...)`` otherwise.
    """
    is_retrieve = spec.kind is SelectorKind.RETRIEVE

    drf_kwargs: dict[str, Any] = {"detail": is_retrieve}
    if methods is not None:
        drf_kwargs["methods"] = methods
    if url_path is not None:
        drf_kwargs["url_path"] = url_path
    if url_name is not None:
        drf_kwargs["url_name"] = url_name
    if spec.permission_classes is not None:
        drf_kwargs["permission_classes"] = list(spec.permission_classes)
    drf_kwargs.update(action_kwargs)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn_label = getattr(fn, "__qualname__", repr(fn))
        validate_selector_spec(spec, label=f"@selector_action {fn_label}")

        @functools.wraps(fn)
        def handler(self: Any, request: Request, *args: Any, **kwargs: Any) -> Response:
            if is_retrieve:
                instance = dispatch_selector_for_spec(self, spec, extra_url_kwargs=self.kwargs)
                serializer = _build_serializer(
                    self, spec, instance, many=False, extras={"instance": instance}
                )
                return Response(serializer.data)

            result = dispatch_selector_for_spec(self, spec, extra_url_kwargs=self.kwargs)
            page = self.paginate_queryset(result) if hasattr(self, "paginate_queryset") else None
            if page is not None:
                serializer = _build_serializer(self, spec, page, many=True, extras={"page": page})
                return self.get_paginated_response(serializer.data)
            serializer = _build_serializer(self, spec, result, many=True, extras={"page": result})
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
    extras: Mapping[str, Any],
) -> Any:
    """Instantiate the response serializer for a ``@selector_action`` result.

    ``spec.output_serializer`` wins when set (matching the standalone
    selector views' override semantics); otherwise the viewset's
    ``get_serializer(...)`` is used so existing
    :class:`ActionSerializerResolver` wiring continues to apply.

    Context flows through the standard three-layer chain:
    DRF default → ``get_output_serializer_context`` (if defined) →
    ``get_<action>_output_serializer_context`` (if defined). The directional
    hook is optional, so plain ``ViewSet`` subclasses get DRF default
    context plus any per-action override they declare.

    ``extras`` carries the resolved data (the ``instance`` for a detail
    action, the ``page`` for a collection action), offered to each context
    provider by keyword when it declares the name.
    """
    if spec.output_serializer is not None:
        action: str | None = getattr(view, "action", None)
        action_hook: str | None = f"get_{action}_output_serializer_context" if action else None
        context = resolve_serializer_context(
            view,
            view.request,
            direction_hook="get_output_serializer_context",
            action_hook=action_hook,
            spec_provider=spec.output_serializer_context,
            extras=extras,
        )
        return spec.output_serializer(instance, many=many, context=context)
    return view.get_serializer(instance, many=many)
