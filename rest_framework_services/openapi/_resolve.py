"""Resolve the active spec for an inspected view, at schema-generation time.

One resolver per spec family, all walking the same three view shapes:
standalone view, ``@*_action``-decorated handler, viewset ``action_specs``
entry.
"""

from __future__ import annotations

from typing import Any

from rest_framework_services.types.polymorphic_service_spec import PolymorphicServiceSpec
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.viewsets.utils import resolve_action_spec_entry


def resolve_polymorphic_spec(view: Any) -> PolymorphicServiceSpec | None:
    """Return the ``PolymorphicServiceSpec`` driving ``view``, if any.

    Standalone ``Service*View`` classes take a single ``ServiceSpec``, so
    ``view.spec`` is never polymorphic and is not checked here.
    """
    action_name = getattr(view, "action", None)
    if action_name is None:
        return None
    handler = getattr(view, action_name, None)
    action_spec = getattr(handler, "_service_spec", None)
    if isinstance(action_spec, PolymorphicServiceSpec):
        return action_spec
    action_specs = getattr(view, "action_specs", None)
    if action_specs is not None:
        entry = resolve_action_spec_entry(action_specs, action_name)
        if isinstance(entry, PolymorphicServiceSpec):
            return entry
    return None


def resolve_service_spec(view: Any) -> ServiceSpec[Any, Any, Any] | None:
    """Return the ``ServiceSpec`` driving ``view``'s current request, if any.

    Checked in order: ``view.spec`` on a standalone view, the ``_service_spec``
    stamp on a ``@service_action`` handler, then the ``action_specs`` entry for
    ``view.action``. The last follows the same ``"partial_update"`` →
    ``"update"`` fallback as runtime dispatch, so the documented PATCH schema
    matches what actually validates. ``None`` when no service-style spec
    applies.
    """
    spec = getattr(view, "spec", None)
    if isinstance(spec, ServiceSpec):
        return spec

    action_name = getattr(view, "action", None)
    if action_name is None:
        return None

    handler = getattr(view, action_name, None)
    action_spec = getattr(handler, "_service_spec", None)
    if isinstance(action_spec, ServiceSpec):
        return action_spec

    action_specs = getattr(view, "action_specs", None)
    if action_specs is not None:
        entry = resolve_action_spec_entry(action_specs, action_name)
        if isinstance(entry, ServiceSpec):
            return entry
    return None


def resolve_selector_spec(view: Any) -> SelectorSpec[Any, Any] | None:
    """Return the ``SelectorSpec`` driving ``view``'s current read, if any.

    The read-side twin of :func:`resolve_service_spec`, walking the same three
    surfaces with ``_selector_spec`` as the handler stamp. The ``action_specs``
    lookup is non-raising plus an ``isinstance`` gate, so a write action whose
    entry is a ``ServiceSpec`` yields ``None`` rather than raising during schema
    generation. Callers additionally gate on ``spec.selector``: a
    ``selector=None`` opt-out never runs its ``filter_set``, so it documents no
    query parameters.
    """
    spec = getattr(view, "spec", None)
    if isinstance(spec, SelectorSpec):
        return spec

    action_name = getattr(view, "action", None)
    if action_name is None:
        return None

    handler = getattr(view, action_name, None)
    action_spec = getattr(handler, "_selector_spec", None)
    if isinstance(action_spec, SelectorSpec):
        return action_spec

    action_specs = getattr(view, "action_specs", None)
    if action_specs is not None:
        entry = resolve_action_spec_entry(action_specs, action_name)
        if isinstance(entry, SelectorSpec):
            return entry
    return None
