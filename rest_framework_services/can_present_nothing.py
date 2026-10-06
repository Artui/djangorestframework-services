"""``can_present_nothing`` — whether a spec's dispatch can present ``None``.

At the package root, beside ``is_async``, because no one subpackage owns the
question: dispatch is what presents the ``None``, the JSON Schema and the
capability manifest state it, and every transport advertising an output schema
should ask it too.
"""

from __future__ import annotations

from typing import Any

from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


def can_present_nothing(spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]) -> bool:
    """Whether a successful dispatch of ``spec`` may present ``None`` as its result.

    The question an output schema has to answer before it admits ``null``, asked
    in one place so that this package's
    [`spec_to_json_schema`][rest_framework_services.jsonschema.spec_to_json_schema.spec_to_json_schema]
    and every transport advertising an output schema can give the same answer:

    - A result that is a list is *declared* never ``None``, only empty: a ``LIST``
      ``SelectorSpec``, a ``many=True`` ``ServiceSpec``, and a ``ServiceSpec``
      whose ``output_selector_spec`` declares ``LIST``, with a ``selector`` to
      re-read through or without one. ``allow_none`` is not read on any of them.
      A callable returning ``None`` there anyway is the author's error. With no
      ``selector`` to re-read, dispatch refuses it. On the other three paths, a
      ``LIST`` selector, a ``LIST`` re-read and a ``many=True`` service, dispatch
      does not refuse it and presents the ``None``, which a transport validating
      structured output against the schema will reject. Held by
      ``test_a_list_callable_returning_none_is_presented_unrefused``.
    - A ``RETRIEVE`` ``SelectorSpec`` presents ``None`` for a miss under
      ``allow_none=True``; without it dispatch answers the miss as
      ``not_found``.
    - A single-row ``ServiceSpec`` whose ``output_selector_spec`` has a
      ``selector`` presents ``None`` when the re-read finds no row, because
      dispatch materializes it with ``.first()``. That needs no declaration,
      and the nested spec's ``allow_none`` is not read.
    - Any other single-row ``ServiceSpec`` presents the service's own return,
      and answers its own ``allow_none``. That is a declaration rather than an
      observation: an undeclared ``None`` is still presented, against a schema
      that does not admit it.

    It answers whether ``None`` *may* come back, not whether a schema exists:
    a spec declaring no output serializer has no output schema to widen, whatever
    this says.
    """
    if isinstance(spec, SelectorSpec):
        # One arc to coverage, so each operand is named:
        # test_a_list_selector_never_presents_nothing holds the kind, and
        # test_a_retrieve_selector_presents_nothing_only_under_allow_none the flag.
        return spec.kind is SelectorKind.RETRIEVE and spec.allow_none
    if spec.many:
        return False
    nested = spec.output_selector_spec
    if nested is None:
        return spec.allow_none
    if nested.kind is SelectorKind.LIST:
        # A set, re-read or the service's own: held by the truth table's
        # ``service-list-declared-but-no-re-read-allow-none`` row.
        return False
    if nested.selector is not None:
        # A row the re-read may not find.
        return True
    # Held by test_a_re_read_spec_without_a_selector_is_no_re_read: without the
    # selector test above, a ``RETRIEVE`` declaration with nothing to re-read
    # would admit a ``None`` its service never declared.
    return spec.allow_none


__all__ = ["can_present_nothing"]
