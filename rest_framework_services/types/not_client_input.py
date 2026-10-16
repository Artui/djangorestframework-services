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
- **The caller's value never reaches the callable.** A name any callable of
  the call marks - the selector or service, each of its ``preconditions``, a
  service's ``instance_selector_spec`` / ``collection_selector_spec`` lookup -
  is the server's for the whole call, so the compiled spec declares no
  parameter of that name: ``dispatch_spec``, ``adispatch_spec`` and the
  kernel's ``dispatch`` drop the caller's value under ``IGNORE``, whatever the
  argument binding. An ``input_serializer`` field of that name stays the
  client's input, validated into ``data``, and the client's value reaches the
  service only there: the parameter is not handed it by name, on either core,
  over HTTP or off it, nor is a precondition.

So a marked key is filled only by a channel the caller does not control: a view
hook's or the ``spec.kwargs`` provider's value, a registered pool seed,
``input_data`` through an ``input_serializer`` field of that name (a
``HiddenField``'s own value where ``input_data`` names one), the parameter's own
default, or, for a selector or a target lookup, a route capture
(``view.kwargs``, or ``build_offline_context(kwargs=...)`` off HTTP). A target
lookup's capture is seeded for the call, so a service parameter of the same
name is handed it as well; a selector's is handed to the selector. A provider
that declines with ``UNSET`` therefore leaves the default in place, never the
caller's value. That moves the hazard rather than removing it: a scoping key
whose default reads as "everything" must have a provider that always resolves
it.

``UnknownArguments.REJECT`` refuses a caller that supplies a marked key, since
the key is no declared argument - which is exactly what it is - unless an
``input_serializer`` field declares it. Where a selector or a target lookup
takes a bare ``**kwargs``, or a service with no ``input_serializer`` takes one
under ``SPREAD_AUTHOR_WINS``, nothing can be called unknown, and the value is
dropped instead.

The marker governs the callable's own parameters. A ``many=True`` service
receives its rows inside one ``data`` list and their values under its
``many_argument``, never by field, so a row's field of a marked name stays in
its row and the parameter is not filled from it.

A value ``input_data`` supplies for a marked key is the server's, not the
caller's. ``input_data`` (with the ``get_input_data`` view hooks) is the spec
author's own code, merged over the caller's input with its keys winning, so
where the caller sends the same key the server's value is the one that arrives.
The kernel refuses ``input_data`` on a spec with no ``input_serializer``, so it
reaches the service through a field of that name: validated, in ``data`` and
by name. A key no field declares is no argument: ``IGNORE`` drops it and
``REJECT``, which judges the merged arguments, refuses it, as it refuses any
``input_data`` key nothing declares.

**An annotation that does not resolve at runtime does not hide the marker.** A
name imported only under ``if TYPE_CHECKING:`` costs only the parameter or key it
annotates, and every other one is read as written. The one it annotates is read
with that name standing in for ``Any``, so ``Annotated[Owner, NotClientInput] |
None`` is hidden; where even that cannot reach the marker (``models.Owner``, or a
marker inside a quoted string), an annotation whose text names ``NotClientInput``
is hidden by that name. A ``**kwargs: Unpack[Extras]`` whose ``TypedDict`` itself
does not resolve hides nothing, because nothing says which keys it has:
``UnknownArguments.REJECT`` refuses to run on it, and the other policies deliver
what the caller sent.

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
