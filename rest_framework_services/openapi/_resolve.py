"""Resolve the active ``ServiceSpec`` for an inspected view (schema-time)."""

from __future__ import annotations

from typing import Any

from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.viewsets.utils import resolve_action_spec_entry


def resolve_spec(view: Any) -> ServiceSpec[Any, Any, Any] | None:
    """Return the ``ServiceSpec`` driving ``view``'s current request, if any.

    Three surfaces, checked in order:

    1. **Standalone single-purpose view** — ``view.spec`` (set by
       ``ServiceCreateView`` / ``ServiceUpdateView`` / ``ServiceDeleteView``
       subclasses).
    2. **Custom action via** ``@service_action`` — the decorated handler
       carries ``_service_spec`` stamped at decoration time.
    3. **Viewset action keyed in** ``action_specs`` — looked up by
       ``view.action`` (DRF sets this on the bound view before the schema
       generator inspects it), following the same action-key fallback chain
       as runtime dispatch (``"partial_update"`` → ``"update"``) so the
       documented PATCH schema matches what actually validates.

    Returns ``None`` when the view is not driven by a service-style spec
    (e.g. a vanilla ``GenericAPIView``, an unset spec, or a read-side
    ``SelectorSpec`` entry).
    """
    spec = getattr(view, "spec", None)
    if isinstance(spec, ServiceSpec):
        return spec

    action_name = getattr(view, "action", None)
    if action_name is None:
        return None

    # ``@service_action``-decorated method.
    handler = getattr(view, action_name, None)
    action_spec = getattr(handler, "_service_spec", None)
    if isinstance(action_spec, ServiceSpec):
        return action_spec

    # ``ServiceCreateMixin`` / ``ServiceUpdateMixin`` / ``ServiceDestroyMixin``.
    action_specs = getattr(view, "action_specs", None)
    if action_specs is not None:
        entry = resolve_action_spec_entry(action_specs, action_name)
        if isinstance(entry, ServiceSpec):
            return entry
    return None
