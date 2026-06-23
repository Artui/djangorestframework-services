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
    belongs to the caller rather than the spec.

    Members (the value never appears on a wire — pass the member directly):

    - ``AUTO`` — resolve per spec type to reproduce the pre-policy behaviour:
      :class:`ServiceSpec` → ``BUNDLE`` (a mutation takes its validated payload
      as one ``data`` bundle), :class:`SelectorSpec` → ``SPREAD_AUTHOR_WINS`` (a
      read spreads its params so the selector can declare them as parameters).
      The default.
    - ``BUNDLE`` — only the validated payload reaches the callable, as ``data=``;
      individual client fields are **not** spread as kwargs. The safe default for
      a mutation whose ``kwargs`` scopes the write — the client cannot inject
      arbitrary keyword arguments.
    - ``SPREAD_AUTHOR_WINS`` — client fields are spread into the pool as
      individual kwargs, but ``spec.kwargs(...)`` **overrides on conflict**. An
      author-scoped ``tenant_id`` cannot be reset by the client. (drf-mcp's
      former ``MERGE``.)
    - ``SPREAD_CALLER_WINS`` — like ``SPREAD_AUTHOR_WINS`` but the client wins on
      conflict: ``spec.kwargs(...)`` supplies *defaults* the client may override.
      (drf-mcp's former ``REPLACE``.)

    Reserved pool seeds (``request`` / ``user`` / ``data`` / ``serializer`` /
    ``instance`` / ``collection``) are always stripped from the spread in the
    ``SPREAD_*`` modes, so a client cannot poison transport-controlled state by
    naming an argument after one of them.
    """

    AUTO = "auto"
    BUNDLE = "bundle"
    SPREAD_AUTHOR_WINS = "spread_author_wins"
    SPREAD_CALLER_WINS = "spread_caller_wins"
