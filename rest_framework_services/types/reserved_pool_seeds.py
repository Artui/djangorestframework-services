"""``RESERVED_POOL_SEEDS`` — pool keys carrying transport-controlled seeds."""

from __future__ import annotations

# A client-supplied argument named after one of these would override the
# dispatcher's authoritative value (a credential-spoofing footgun), so the
# ``SPREAD_*`` argument-binding modes strip them from the spread. The dispatched
# callable may still *declare* a parameter of that name — it receives the seed,
# the documented idiom.
#
# Public because transport adapters need the same list: anything that lets a
# caller route a value into the kwargs pool (an MCP tool argument, an agent
# tool's ``UrlKwarg`` / ``QueryParam``) must refuse these names for exactly the
# reason above. Adapters must import this rather than keep a copy — a local copy
# silently stops tracking the set it is meant to mirror.
RESERVED_POOL_SEEDS: frozenset[str] = frozenset(
    {"request", "user", "data", "serializer", "instance", "collection"}
)

__all__ = ["RESERVED_POOL_SEEDS"]
