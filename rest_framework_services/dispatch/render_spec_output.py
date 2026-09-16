"""``render_spec_output`` — render a dispatch result through the spec's serializer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.audience.utils import rendered_affordances, with_affordances
from rest_framework_services.dispatch.renderable_serializer_class import renderable_serializer_class
from rest_framework_services.dispatch.utils import output_serializer_for, resolve_output_context
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.view_hooks import ViewHooks


def render_spec_output(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    value: Any,
    *,
    many: bool = False,
    view: Any = None,
    request: Any = None,
    extras: Mapping[str, Any] | None = None,
    view_hooks: ViewHooks | None = None,
) -> Any:
    """Render ``value`` to a JSON-shaped payload using ``spec``'s output serializer.

    The blessed render step that pairs with
    [`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec]:
    every transport renders the same way instead of re-implementing serializer + context
    plumbing. Reads the output serializer from ``spec.output_serializer`` (selector) or
    ``spec.output_selector_spec.output_serializer`` (service), a raw dataclass type
    rendering through a ``DataclassSerializer`` for it; when none is set the value
    passes through (list-coerced when ``many=True`` so a queryset evaluates).

    The serializer ``context`` always carries DRF's baseline — ``request`` / ``format``
    / ``view``, from
    [`base_serializer_context`][rest_framework_services.dispatch.base_serializer_context.base_serializer_context]
    — so a serializer that reads ``self.context["request"]`` renders identically here
    and behind a DRF view. The spec's ``output_serializer_context`` provider, if any, is
    resolved with ``view`` / ``request`` plus the resolved-data ``extras`` it declares
    (``page`` for a list, ``instance`` for a retrieve, ``result`` for a mutation) and
    merged over that baseline.

    Pagination is the caller's job — pass the already-sliced page as ``value``
    (and the same page object under the matching ``extras`` key) so an
    id-keyed batched context query reuses the page's result cache.

    **Affordances.** When the selector spec rendered through declares
    ``affordances``, each rendered object gains an ``affordances`` key: per
    declared name, ``{"available": true}``, or ``{"available": false, "code": ...,
    "reason": ...}`` naming the first condition the row does not meet; ``None`` is
    not a row and carries none. The answers are read off the rows as the
    selector's dispatch left them -- annotations on a queryset's rows, or the
    answers it attached to rows a selector returned directly -- so rendering them
    costs no query; ``many`` rows are materialised once so the rows walked are the
    rows rendered. The ``reason`` is for a human reader --
    [`render_for_audience`][rest_framework_services.dispatch.render_for_audience.render_for_audience]
    leaves it out. A spec declaring none renders exactly as before.
    """
    serializer_cls = renderable_serializer_class(output_serializer_for(spec))
    affordances = rendered_affordances(spec)
    if serializer_cls is None:
        if affordances is not None:
            raise ImproperlyConfigured(
                "affordances are projected into each rendered object, and this spec "
                "declares no output_serializer to render one. Declare an output_serializer."
            )
        if many:
            return list(value) if hasattr(value, "__iter__") else value
        return value
    if affordances is not None and many:
        # Walked twice -- once by the serializer, once for the answers -- so it is
        # materialised here rather than trusted to cache: a manager passed as the
        # value would be re-queried on the second walk.
        value = list(value)
    context = resolve_output_context(
        spec, view=view, request=request, extras=extras or {}, view_hooks=view_hooks
    )
    payload: Any = serializer_cls(value, many=many, context=context).data
    if affordances is None or value is None:
        return payload
    return with_affordances(payload, value, affordances, many=many)


__all__ = ["render_spec_output"]
