"""``UnknownArguments`` — how strict ``dispatch_spec`` is about undeclared input keys."""

from __future__ import annotations

from enum import Enum


class UnknownArguments(Enum):
    """How ``dispatch_spec`` treats ``params`` keys outside a spec's declared set.

    The *declared set* is derived from the spec without any transport knowledge:
    a :class:`ServiceSpec`'s ``input_serializer`` fields plus the keys its nested
    target selectors consume; a :class:`SelectorSpec`'s ``selector`` parameters.
    When the set cannot be enumerated — a callable that declares ``**kwargs``, or
    a duck-typed ``filter_set`` whose fields are opaque to the core — the spec is
    treated as **open** and this policy is a no-op (there is nothing to call
    "unknown").

    Members (internal knob — the value never appears on a wire):

    - ``IGNORE`` — undeclared keys are dropped (DRF serializers already do this
      to a mutation's payload, and a selector simply never receives kwargs it
      doesn't declare). The default, reproducing the pre-policy behaviour.
    - ``REJECT`` — an undeclared key raises
      :exc:`~rest_framework.exceptions.ValidationError`, the same surface a
      strict serializer produces. Useful when the caller wants a clean
      correction signal (e.g. a model calling a tool with a mistyped argument).
    - ``PASSTHROUGH`` — undeclared keys survive: they are merged onto the
      mutation's ``validated_data`` before the keyword pool is built, so a
      callable that declares them (or ``**kwargs``) receives them. The one
      policy that *must* live inside ``dispatch_spec`` — it needs the seam
      between validation and pool construction that a wrapper cannot reach.

    Callers strip their own transport-only keys (pagination, ordering, output
    format) before calling, so the declared-set check sees only spec inputs.
    """

    IGNORE = "ignore"
    REJECT = "reject"
    PASSTHROUGH = "passthrough"
