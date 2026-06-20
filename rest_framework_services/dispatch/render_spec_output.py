"""``render_spec_output`` — render a dispatch result through the spec's serializer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services.dispatch.utils import output_serializer_for, resolve_output_context
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


def render_spec_output(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    value: Any,
    *,
    many: bool = False,
    view: Any = None,
    request: Any = None,
    extras: Mapping[str, Any] | None = None,
) -> Any:
    """Render ``value`` to a JSON-shaped payload using ``spec``'s output serializer.

    The blessed render step that pairs with
    :func:`~rest_framework_services.dispatch_spec`: every transport renders the
    same way instead of re-implementing serializer + context plumbing. Reads
    the output serializer from ``spec.output_serializer`` (selector) or
    ``spec.output_selector_spec.output_serializer`` (service); when none is set
    the value passes through (list-coerced when ``many=True`` so a queryset
    evaluates).

    The spec's ``output_serializer_context`` provider, if any, is resolved with
    ``view`` / ``request`` plus the resolved-data ``extras`` it declares
    (``page`` for a list, ``instance`` for a retrieve, ``result`` for a
    mutation) and forwarded as the serializer ``context``.

    Pagination is the caller's job — pass the already-sliced page as ``value``
    (and the same page object under the matching ``extras`` key) so an
    id-keyed batched context query reuses the page's result cache.
    """
    serializer_cls = output_serializer_for(spec)
    if serializer_cls is None:
        if many:
            return list(value) if hasattr(value, "__iter__") else value
        return value
    context = resolve_output_context(spec, view=view, request=request, extras=extras or {})
    if context is None:
        return serializer_cls(value, many=many).data
    return serializer_cls(value, many=many, context=context).data


__all__ = ["render_spec_output"]
