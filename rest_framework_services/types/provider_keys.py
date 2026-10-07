"""``ProviderKeys`` — the names a ``kwargs=`` provider fills, and those it may decline."""

from __future__ import annotations

from typing import NamedTuple


class ProviderKeys(NamedTuple):
    """What
    [`provider_keys`][rest_framework_services.dispatch.provider_keys.provider_keys]
    read off a ``kwargs=`` provider, before it runs.

    Two disjoint sets of key names that together are every key of the
    ``TypedDict`` the provider is annotated to return.

    A ``NamedTuple`` so that it reads both ways its consumers read it. It still
    unpacks as ``filled, declinable = provider_keys(spec.kwargs)``, which is how
    the MCP server's call site reads it, and it answers ``.filled`` and
    ``.declinable``, which is what the Pydantic-AI toolset's private dataclass
    offered before this replaced it. Both are held by
    ``test_the_answer_names_its_two_sets_and_still_unpacks``.

    Attributes:
        filled: The keys the provider always answers for, ``NotRequired`` ones
            included, since the provider owns them. A transport need not ask
            its caller for any of them.
        declinable: The keys whose value admits ``UnsetType``
            (``tenant: str | UnsetType``). The provider may return ``UNSET`` for
            one, which dispatch removes from the pool, so the caller's value is
            the one the callable receives. Such a key is the caller's to send,
            but not required of it, since the provider may fill it after all.
    """

    filled: frozenset[str]
    declinable: frozenset[str]
