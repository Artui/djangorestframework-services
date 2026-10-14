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
    argument: a caller's value named like one is dropped, so a client cannot
    poison transport-controlled state by naming an argument after one.

    What is left for this enum is one question about a service spec with no
    ``input_serializer``: whether the service's own parameters are caller
    input. A selector spec's reads are the caller's under every member. It
    is a trust-boundary decision, which is why it belongs to the caller
    rather than the spec. Pass the member directly; the value never appears
    on a wire.

    Attributes:
        AUTO: The default, resolved per spec type: [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] →
            ``BUNDLE``, [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec] →
            ``SPREAD_AUTHOR_WINS``, which changes nothing for a selector.
        BUNDLE: A service's own parameters are never the caller's: the
            validated payload reaches it as ``data=`` and its declared reads
            by name, and nothing else the client sends. The safe choice for a
            mutation whose ``kwargs`` scopes the write.
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
