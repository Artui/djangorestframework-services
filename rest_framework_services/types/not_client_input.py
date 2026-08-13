"""The ``NotClientInput`` schema marker and its type.

Marks a declared input as **provider-owned**: it is dropped from the generated
schema entirely, so a schema-driven caller never learns it exists and never
supplies it. Use it inside ``Annotated[...]`` on an extras ``TypedDict`` key (or
an ordinary parameter) of a service / selector::

    class WidgetExtras(HttpExtras[MyUser], total=False):
        project_pk: Annotated[int, InputRequired]
        team_role: Annotated[str, NotClientInput]  # resolved by spec.kwargs

**Why.** Reflecting ``Unpack[<TypedDict>]`` into the input schema makes every
declared key visible to an LLM / MCP client — including keys a ``spec.kwargs``
provider supplies from request state, which the caller has no business setting.
Advertising those is merely a wart on the *scoping* keys, because the selector
default ``SPREAD_AUTHOR_WINS`` plus an always-resolving provider makes a
client-supplied value dead on arrival. ``NotClientInput`` removes the wart at the
source rather than relying on that invariant to absorb it.

The marker is **advertisement-only**, never delivery or enforcement: a marked key
is still spread into the kwargs pool exactly as before, and the provider still
resolves it. It also does **not** make the key safe on its own — the security
property is still the author-wins precedence documented in ``resolve_provider``.
Marking a key hidden while opting into ``SPREAD_CALLER_WINS`` on a scoped spec
voids that guarantee just as it did before, and a provider owning a scoping key
must still never decline via ``UNSET``.

A key marked ``NotClientInput`` is also excluded from
``declared_input_keys``, so ``UnknownArguments.REJECT`` treats a caller that
supplies it as passing an unknown argument — which is exactly what it is.

Its counterpart is ``InputRequired``, which marks a
key mandatory. A key marked with both is a contradiction and raises at
schema-generation time.

``NotClientInput`` is the singleton you place in the annotation.
``NotClientInputType`` is its type, exported only so the singleton can be spelled
in annotations; you never need to instantiate it.
"""

from __future__ import annotations


class NotClientInputType:
    """Singleton marker type. Identity-equal to itself only.

    Don't instantiate this directly — use the module-level ``NotClientInput``
    singleton. ``NotClientInputType()`` returns that same instance.
    """

    _instance: NotClientInputType | None = None

    def __new__(cls) -> NotClientInputType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "NotClientInput"


NotClientInput: NotClientInputType = NotClientInputType()

__all__ = ["NotClientInput", "NotClientInputType"]
