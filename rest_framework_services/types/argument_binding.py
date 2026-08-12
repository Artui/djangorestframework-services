"""``ArgumentBinding`` — how a caller's flat input maps onto a dispatched callable."""

from __future__ import annotations

from enum import Enum


class ArgumentBinding(Enum):
    """How ``dispatch_spec`` turns the flat ``params`` into a callable's kwargs.

    Every dispatched callable's keyword pool always carries the ``request`` /
    ``user`` seeds (and, for a mutation, ``data`` / ``serializer`` / ``instance``
    / ``collection``). This enum controls the *one* remaining question a caller
    answers about its wire: **can client-supplied input land as individual
    keyword arguments, and do those override the spec author's ``kwargs(...)``
    invariants?** It is a trust-boundary decision, not plumbing — which is why it
    belongs to the caller rather than the spec. Pass the member directly; the
    value never appears on a wire. Those reserved seeds are always stripped from
    the spread in the ``SPREAD_*`` modes, so a client cannot poison
    transport-controlled state by naming an argument after one of them.

    Attributes:
        AUTO: The default — resolve per spec type: :class:`ServiceSpec` →
            ``BUNDLE`` (a mutation takes its validated payload as one ``data``
            bundle), :class:`SelectorSpec` → ``SPREAD_AUTHOR_WINS`` (a read
            spreads its params so the selector can declare them as parameters).
        BUNDLE: Only the validated payload reaches the callable, as ``data=``;
            individual client fields are **not** spread as kwargs. The safe
            choice for a mutation whose ``kwargs`` scopes the write — the client
            cannot inject arbitrary keyword arguments.
        SPREAD_AUTHOR_WINS: Client fields are spread into the pool as individual
            kwargs, but ``spec.kwargs(...)`` **overrides on conflict** — an
            author-scoped ``tenant_id`` cannot be reset by the client.
        SPREAD_CALLER_WINS: Like ``SPREAD_AUTHOR_WINS``, but the client wins on
            conflict: ``spec.kwargs(...)`` supplies *defaults* it may override.
    """

    AUTO = "auto"
    BUNDLE = "bundle"
    SPREAD_AUTHOR_WINS = "spread_author_wins"
    SPREAD_CALLER_WINS = "spread_caller_wins"
