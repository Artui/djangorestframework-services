"""``PoolSeeds`` — a project's own always-available kwargs-pool seeds."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS

# A single registration: ``(name, resolver)``. A tuple rather than a mapping so
# the registry stays frozen and the resolution order is the registration order,
# which is what makes an error message or a listing reproducible.
_Seed = tuple[str, Callable[..., Any]]


@dataclass(frozen=True)
class PoolSeeds:
    """Names a project adds to every dispatched callable's kwargs pool.

    The seven names in ``RESERVED_POOL_SEEDS`` are the dispatcher's own. Everything else a service legitimately needs — a
    tenant, a correlation id, a locale, a clock, a feature-flag reader — has no
    channel, because on HTTP all of it hangs off ``request`` and **off HTTP
    ``request`` is the thing you do not have**. This is that channel.

    A registration contributes three things, through two mechanisms:

    - **the value**, resolved into every pool
      [`base_pool`][rest_framework_services.dispatch.base_pool.base_pool] builds;
    - **reservation** — client input named after a seed is stripped from the
      spread, so a caller cannot outrank the project's value;
    - **exemption** from unknown-argument accounting, so declaring a seed does
      not make [`UnknownArguments.REJECT`][rest_framework_services.types.unknown_arguments.UnknownArguments]
      start refusing calls that name it.

    The last two are one mechanism — ``reserved`` — and the reason they travel
    together is that either alone is a trap. A seed with a value and no
    reservation is client-controlled on a selector, where no validator stands in
    front of the spread; a reserved name with no value makes a callable that
    declares it fail with a ``TypeError`` instead.

    Immutable, and ``extend`` returns a **new** registry rather than mutating, so
    there is no shared global state to leak between mounts or across tests —
    the same shape as
    [`JsonSchemaRegistry`][rest_framework_services.types.json_schema_registry.JsonSchemaRegistry].
    Pass one per dispatch rather than installing it process-wide: a project that
    mounts two endpoints with different ambient context needs them to differ, and
    a module-level default cannot.

        seeds = DEFAULT_POOL_SEEDS.extend(tenant=lambda *, user: user.tenant)
        dispatch_spec(spec, user=user, params=params, pool_seeds=seeds)

    A resolver is called through the same declare-to-receive rule as every other
    spec callable: it names the pool entries it wants (``user``, ``request``, an
    adapter's own entry) and receives only those, or takes ``**kwargs`` for all
    of them.

    Attributes:
        seeds: The registrations, in registration order.
    """

    seeds: tuple[_Seed, ...] = ()

    def extend(self, **seeds: Callable[..., Any]) -> PoolSeeds:
        """Return a new registry with ``seeds`` appended.

        Raises:
            ValueError: A name is one of the dispatcher's own, or is already
                registered here. Both are refused at **registration** rather
                than at dispatch, because a silent last-wins is the exact
                failure the reservation exists to prevent — and a collision
                discovered on the hot path is one a test suite can miss.
        """
        for name in seeds:
            if name in RESERVED_POOL_SEEDS:
                raise ValueError(
                    f"{name!r} is a reserved pool seed carrying the dispatcher's own "
                    f"value; registering it would shadow what the transport resolved. "
                    f"Reserved: {sorted(RESERVED_POOL_SEEDS)}."
                )
            if name in self.names:
                raise ValueError(
                    f"{name!r} is already registered on this PoolSeeds. Registering it "
                    f"twice would make which resolver wins depend on registration order."
                )
        return replace(self, seeds=self.seeds + tuple(seeds.items()))

    @property
    def names(self) -> frozenset[str]:
        """The registered names, without their resolvers."""
        return frozenset(name for name, _ in self.seeds)

    @property
    def reserved(self) -> frozenset[str]:
        """Every name client input may not occupy: the dispatcher's plus these.

        The four places that strip or exempt a name read this rather than
        ``RESERVED_POOL_SEEDS`` directly, which is what makes a registered seed
        as protected as a built-in one.
        """
        return RESERVED_POOL_SEEDS | self.names

    def resolvers(self) -> Mapping[str, Callable[..., Any]]:
        """The registrations as a mapping, for a caller that resolves them.

        Resolution itself lives in
        [`base_pool`][rest_framework_services.dispatch.base_pool.base_pool] rather
        than here: it needs the declare-to-receive helper, and ``types/`` is the
        dependency sink — it may not import from the behavioural packages.
        """
        return dict(self.seeds)


DEFAULT_POOL_SEEDS: PoolSeeds = PoolSeeds()
"""The empty registry every dispatch entry point falls back to.

A project that registers nothing gets exactly the behaviour that existed before
this type: the seven built-in seeds, and nothing else.
"""

__all__ = ["DEFAULT_POOL_SEEDS", "PoolSeeds"]
