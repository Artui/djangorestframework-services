"""``AffordanceDeclarer`` -- what a ``SelectorSpec`` asks of each spec in its ``affordances``."""

from __future__ import annotations

from collections.abc import Sequence

from rest_framework_services.types.affordance import Affordance


class AffordanceDeclarer:
    """A spec that declares the conditions an operation needs: a ``ServiceSpec``.

    A ``SelectorSpec`` maps each name in its ``affordances`` to the
    ``ServiceSpec`` whose conditions it answers for every row, and reads one
    attribute off it: ``affordances``. It cannot name ``ServiceSpec`` to type
    that mapping or to refuse anything else in it, because ``service_spec``
    imports ``selector_spec`` for the selector specs a service declares (its
    instance, collection and output selectors), and the two modules
    importing each other fail whichever loads first. So ``ServiceSpec``
    inherits this base, which imports nothing of either, and the selector
    names the base.

    A base rather than a protocol because ``SelectorSpec`` refuses, when it is
    built, any value of that mapping that is not one: an ``isinstance`` against
    a protocol of this one attribute would pass a ``SelectorSpec``, which has
    an ``affordances`` field of its own, and the refusal would let through the
    spec of a read where the spec of a write is meant.
    """

    affordances: Sequence[Affordance] | None
