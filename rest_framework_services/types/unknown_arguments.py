"""``UnknownArguments`` — how strict ``dispatch_spec`` is about undeclared input keys."""

from __future__ import annotations

from enum import Enum


class UnknownArguments(Enum):
    """How ``dispatch_spec`` treats ``params`` keys outside a spec's declared set.

    The *declared set* is derived from the spec and the dispatch's
    ``argument_binding``, without any transport knowledge: a
    [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec]'s
    ``input_serializer`` fields plus the keys of the one target lookup dispatch calls
    (its ``collection_selector_spec`` when declared, else its
    ``instance_selector_spec``, and neither for ``many=True``); a
    [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec]'s
    ``selector`` parameters. A ``ServiceSpec`` with no ``input_serializer``,
    dispatched under a ``SPREAD_*`` binding, also declares its service's own
    parameters, since nothing else reads the caller's input there: its keyword
    parameters and ``Unpack[TypedDict]`` keys, less the reserved pool seeds
    (registered ones included), positional-only parameters, ``NotClientInput`` keys
    and ``view``. ``BUNDLE``, which ``AUTO`` resolves to for a service, leaves them
    out. When the set cannot be enumerated — a callable that declares a bare
    ``**kwargs``, or a duck-typed ``filter_set`` whose fields are opaque to the core —
    the spec is treated as **open** and this policy is a no-op (there is nothing to
    call "unknown").

    Members (internal knob — the value never appears on a wire):

    - ``IGNORE`` — undeclared keys are dropped (DRF serializers already do this
      to a mutation's payload, and a selector simply never receives kwargs it
      doesn't declare). The default, reproducing the pre-policy behaviour.
    - ``REJECT`` — an undeclared key raises
      ``ValidationError``, the same surface a
      strict serializer produces. Useful when the caller wants a clean
      correction signal (e.g. a model calling a tool with a mistyped argument).
    - ``PASSTHROUGH`` — undeclared keys survive: they are merged onto the
      mutation's ``validated_data`` before the keyword pool is built, so a
      callable that declares them (or ``**kwargs``) receives them. The one
      policy that *must* live inside ``dispatch_spec`` — it needs the seam
      between validation and pool construction that a wrapper cannot reach.

    A key the callable marks
    [`NotClientInput`][rest_framework_services.types.not_client_input] is outside
    the declared set, so ``REJECT`` refuses it on a closed spec. Under every other
    policy, and on an open spec, dispatch drops the caller's value for it before the
    spread: ``IGNORE`` drops it as it says, and ``PASSTHROUGH`` never forwards it.

    Callers strip their own transport-only keys (pagination, ordering, output
    format) before calling, so the declared-set check sees only spec inputs.
    """

    IGNORE = "ignore"
    REJECT = "reject"
    PASSTHROUGH = "passthrough"
