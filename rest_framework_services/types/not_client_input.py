"""The ``NotClientInput`` schema marker and its type.

Marks a declared input as **provider-owned**: it is dropped from the generated
schema entirely, so a schema-driven caller never learns it exists and never
supplies it. Use it inside ``Annotated[...]`` on an extras ``TypedDict`` key (or
an ordinary parameter) of a service / selector:

    class WidgetExtras(HttpExtras[MyUser], total=False):
        project_pk: Annotated[int, InputRequired]
        team_role: Annotated[str, NotClientInput]  # resolved by spec.kwargs

**Why.** Reflecting ``Unpack[<TypedDict>]`` into the input schema makes every
declared key visible to an LLM / MCP client — including keys a ``spec.kwargs``
provider supplies from request state, which the caller has no business setting.

**What it does.** Two things, and the second is what makes the first honest:

- **It is not advertised.** The key is dropped from the generated schema.
- **The caller's value never reaches the callable.** ``dispatch_spec`` and
  ``adispatch_spec`` drop the caller's value for a marked key before spreading
  the caller's input into the callable's pool, under every
  ``UnknownArguments`` policy and whatever the argument binding's precedence. That
  covers a ``SelectorSpec``'s selector, a service whose input is spread (and the
  extras ``PASSTHROUGH`` would forward to it), and a service's
  ``instance_selector_spec`` / ``collection_selector_spec`` lookup, whose pool is
  spread from the caller's arguments too.

So a marked key is filled only by a channel the caller does not control: a
``spec.kwargs`` provider, a route capture (``view.kwargs``, or
``build_offline_context(kwargs=…)`` off HTTP), a registered pool seed, or the
parameter's own default. A provider that declines with ``UNSET`` therefore leaves
the default in place, never the caller's value. That moves the hazard rather than
removing it: a scoping key whose default reads as "everything" must have a provider
that always resolves it.

``UnknownArguments.REJECT`` still refuses a caller that supplies a marked key when
the spec's declared set is closed, since the key is excluded from
``declared_input_keys`` and so is an unknown argument — which is exactly what it
is. Where the set is open (a ``filter_set``, a bare ``**kwargs``) nothing can be
called unknown, and the value is dropped instead.

The marker governs the callable's own parameters. A ``many=True`` service receives
its items inside one ``data`` list, never spread, so they are not read for it; and
a field the ``input_serializer`` declares under the same name stays in ``data``,
which is that serializer's payload, while the parameter is still not filled from
it. Over HTTP a selector view spreads nothing, so only the target lookups, whose
pool is spread from the request body, see the difference there.

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
