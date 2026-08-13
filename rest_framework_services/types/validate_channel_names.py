"""``validate_channel_names`` — fail-fast validation for adapter channel declarations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS


class _ChannelDeclaration(Protocol):
    """The shape shared by [`UrlKwarg`][rest_framework_services.types.url_kwarg.UrlKwarg] and [`QueryParam`][rest_framework_services.types.query_param.QueryParam].

    Members are **read-only properties**, not bare attributes. Both concrete
    types are ``@dataclass(frozen=True)``, whose fields are read-only — a bare
    ``name: str`` on a Protocol declares a *mutable* attribute, which a frozen
    dataclass cannot satisfy, so every adapter passing its own tuple would fail
    type-checking at the call site.
    """

    @property
    def name(self) -> str: ...

    @property
    def default(self) -> Any: ...


def validate_channel_names(
    *,
    label: str,
    kind: str,
    declarations: Sequence[_ChannelDeclaration],
    reserved: frozenset[str] = frozenset(),
) -> None:
    """Raise ``ImproperlyConfigured`` on a bad channel declaration set.

    A [`UrlKwarg`][rest_framework_services.types.url_kwarg.UrlKwarg] /
    [`QueryParam`][rest_framework_services.types.query_param.QueryParam] is popped out of the caller's
    arguments and routed to a side channel, so its name must not collide with a
    key the transport controls, and must not be declared twice.

    Three failure modes, all caught at registration time rather than on a call:

    - **Reserved-name collision.** ``RESERVED_POOL_SEEDS`` is always
      included — those are the dispatcher's authoritative seeds, and letting a
      caller route a value onto one is the spoofing footgun the spread modes
      strip. ``reserved`` adds the transport's own keys on top; pass the
      pagination names the transport reserves (``page`` / ``limit`` and whichever
      of ``order`` / ``ordering`` it uses), since those genuinely differ per
      transport while the seed set does not.
    - **Duplicate names** within the set — the later declaration would silently
      shadow the earlier one.
    - **``required`` together with a ``default``** — contradictory: a default
      means the argument can always be satisfied without the caller, so demanding
      it is either a no-op or a lie. Only checked on declarations that carry a
      ``required`` attribute (``QueryParam`` deliberately has none).

    Adapters should call this once per tool / operation, with ``label``
    identifying the offending registration site and ``kind`` naming the
    parameter the consumer passed (``"url_kwargs"``, ``"query_params"``), so the
    message points at something the consumer can act on.
    """
    names = [declaration.name for declaration in declarations]
    collisions = sorted(set(names) & (RESERVED_POOL_SEEDS | reserved))
    if collisions:
        raise ImproperlyConfigured(
            f"{label}: {kind} name(s) {collisions} collide with reserved transport keys "
            f"{sorted(RESERVED_POOL_SEEDS | reserved)}. Rename them."
        )
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ImproperlyConfigured(f"{label}: duplicate {kind} name(s) {duplicates}.")
    contradictory = sorted(
        declaration.name
        for declaration in declarations
        if getattr(declaration, "required", False) and declaration.default is not None
    )
    if contradictory:
        raise ImproperlyConfigured(
            f"{label}: {kind} name(s) {contradictory} set both `required=True` and a "
            "`default` — a declaration with a default is always satisfied, so it "
            "cannot also be required. Drop one."
        )


__all__ = ["validate_channel_names"]
