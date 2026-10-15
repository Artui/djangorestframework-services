"""``ArgumentBinding`` — how a caller's flat input maps onto a dispatched callable."""

from __future__ import annotations

from enum import Enum


class ArgumentBinding(Enum):
    """Whether a caller's values may fill a service's own parameters.

    A call has one pool, and every dispatched callable takes its keyword
    arguments from it by name: the seeds (``user``, ``progress``, ``request``,
    ``view``, a project's registered ones and, for a mutation, ``data``,
    ``serializer``, ``instance`` / ``collection``), the arguments the spec
    declares - its reads, its filter's names, its input serializer's fields -
    and the author's values from a view's hooks and the spec's ``kwargs``
    provider. An author's value for a declared argument replaces the
    caller's, so the author wins a conflict, and a key a typed provider is
    annotated to fill is never the caller's at all. A seed's name is never an
    argument: a caller's value named like one is dropped (``view`` aside,
    which ``UnknownArguments.REJECT`` refuses), and an input serializer's
    field of that name is validated into ``data`` while the seed keeps its
    own value, so a client cannot poison transport-controlled state by naming
    an argument after one. A name any callable of the call marks
    ``NotClientInput`` is the server's: the client's value for an input
    serializer's field of that name reaches the service only inside ``data``,
    while an author's value for it (a view hook's, the spec's ``kwargs``
    provider's or ``input_data``'s) still reaches the parameter.

    What is left for this enum is one question, about a service spec with no
    ``input_serializer`` and about a selector spec: whether the callable's own
    parameters are caller input. A selector spec's declared ``reads`` are the
    caller's under every member. It
    is a trust-boundary decision, which is why it belongs to the caller
    rather than the spec. Pass the member directly; the value never appears
    on a wire.

    Attributes:
        AUTO: The default, resolved per spec type: [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] →
            ``BUNDLE``, [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] →
            ``SPREAD_AUTHOR_WINS``, so a selector takes the caller's value for
            each parameter its signature names.
        BUNDLE: A service's own parameters are never the caller's: the
            validated payload reaches it as ``data=``, and each validated
            value by its key as well (one a callable of the call marks
            ``NotClientInput`` only inside ``data``), beside its declared
            reads by name; nothing else the client sends reaches it. A
            service with no ``input_serializer`` takes ``data={}``. The safe
            choice for a mutation whose ``kwargs`` scopes the write. A
            selector takes none of the caller's values for its parameters
            either: only a route capture, an author's value, a seed and its
            declared reads fill them, the filter still reads the caller's
            values, and a parameter nothing fills is the callable's own
            ``TypeError``. A selector view dispatches this member.
        SPREAD_AUTHOR_WINS: A service with no ``input_serializer`` declares
            its own parameters as the caller's input, and the author's values
            still replace the caller's on a conflict, so an author-scoped
            ``tenant_id`` cannot be reset by the client. A service with an
            input serializer keeps the serializer as its input.
        SPREAD_CALLER_WINS: Retired. ``dispatch_spec`` refuses it with
            ``ValueError``: one pool has no place for a caller's value to beat
            the author's. The member stays importable.
    """

    AUTO = "auto"
    BUNDLE = "bundle"
    SPREAD_AUTHOR_WINS = "spread_author_wins"
    SPREAD_CALLER_WINS = "spread_caller_wins"
