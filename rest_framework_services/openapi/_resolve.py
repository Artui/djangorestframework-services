"""Resolve the active spec for an inspected view (schema-time).

Two sibling resolvers, one per spec family:

- :func:`resolve_service_spec` — the write-side :class:`ServiceSpec` that
  drives request/response bodies on mutation surfaces.
- :func:`resolve_selector_spec` — the read-side :class:`SelectorSpec` that
  drives ``filter_set`` query parameters on list/retrieve surfaces.

Both walk the same three view shapes (standalone view, ``@*_action``-decorated
handler, viewset ``action_specs`` entry) so the schema generator can recover
whichever spec family applies to the operation under inspection.
"""

from __future__ import annotations

from typing import Any

from rest_framework_services.types.polymorphic_service_spec import PolymorphicServiceSpec
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.viewsets.utils import resolve_action_spec_entry


def resolve_polymorphic_spec(view: Any) -> PolymorphicServiceSpec | None:
    """Return the ``PolymorphicServiceSpec`` driving ``view``, if any.

    Walks the same surfaces as :func:`resolve_service_spec` — ``@service_action``
    handler stamp and the viewset ``action_specs`` entry — so the schema
    generator can render a polymorphic request body as the union of its variant
    input serializers. (Standalone ``Service*View`` classes take a single
    ``ServiceSpec``, so ``view.spec`` is never polymorphic.)
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


def resolve_selector_spec(view: Any) -> SelectorSpec[Any, Any] | None:
    """Return the ``SelectorSpec`` driving ``view``'s current read, if any.

    The read-side twin of :func:`resolve_service_spec`, walking the same
    three surfaces:

    1. **Standalone selector view** — ``view.spec`` (set by
       ``SelectorListView`` / ``SelectorRetrieveView`` subclasses).
    2. **Custom action via** ``@selector_action`` — the decorated handler
       carries ``_selector_spec`` stamped at decoration time.
    3. **Viewset action keyed in** ``action_specs`` — looked up by
       ``view.action`` (the ``SelectorListMixin`` / ``SelectorRetrieveMixin``
       ``"list"`` / ``"retrieve"`` entries). Resolved through the non-raising
       :func:`resolve_action_spec_entry` + an ``isinstance`` gate — the same
       shape as :func:`resolve_service_spec` — so a write action whose entry
       is a ``ServiceSpec`` yields ``None`` here rather than raising during
       schema generation.

    Returns ``None`` when no read-side spec applies (a vanilla read view, an
    unset spec, or a write-side ``ServiceSpec`` entry). Used on the schema
    path to emit ``filter_set`` query parameters; the caller additionally
    gates on ``spec.selector`` so a ``selector=None`` opt-out (whose
    ``filter_set`` never runs) documents no parameters.
    """
    spec = getattr(view, "spec", None)
    if isinstance(spec, SelectorSpec):
        return spec

    action_name = getattr(view, "action", None)
    if action_name is None:
        return None

    # ``@selector_action``-decorated method.
    handler = getattr(view, action_name, None)
    action_spec = getattr(handler, "_selector_spec", None)
    if isinstance(action_spec, SelectorSpec):
        return action_spec

    # ``SelectorListMixin`` / ``SelectorRetrieveMixin``.
    action_specs = getattr(view, "action_specs", None)
    if action_specs is not None:
        entry = resolve_action_spec_entry(action_specs, action_name)
        if isinstance(entry, SelectorSpec):
            return entry
    return None
