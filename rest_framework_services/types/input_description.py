"""The ``InputDescription`` schema marker.

Carries prose for a **reflected** input — one the schema derives from a callable's
annotations rather than from a serializer field. Use it inside ``Annotated[...]``
on an extras ``TypedDict`` key (or an ordinary parameter) of a service /
selector:

    class WidgetExtras(HttpExtras[MyUser], total=False):
        project_pk: Annotated[
            int, InputRequired, InputDescription("The project whose widgets to list.")
        ]

    @implements(ListSelector[Widget])
    def list_widgets(**extras: Unpack[WidgetExtras]) -> list[Widget]:
        return Widget.objects.filter(project_id=extras["project_pk"])

The text lands in the generated schema as ``description`` — the same key the
serializer path fills from a field's ``help_text`` and the filter path from a
filter's, so one project's inputs read the same way whichever side of a spec
they arrive on.

**Why a marker was needed at all.** The other two markers, ``InputRequired``
and ``NotClientInput``, are singletons: ``__new__`` returns the one instance, so
neither can carry per-field text even in principle. Without a third marker a reflected key reaches
a schema-driven caller as a bare typed property, and the only way to say what it
means was to declare the same input twice — once in the ``TypedDict`` the
callable actually reads and once in a serializer written for one transport to
describe it with. Two declarations of one input drift, and the second one is the
one nothing executes.

**Why not ``typing_extensions.Doc``.** It is importable on every supported
version, so this is not a dependency question. PEP 727 was **withdrawn**, which
makes ``Doc`` a marker with no standing standard behind it: nothing obliges it to
keep its meaning, and a kernel whose public surface is built on it inherits that.

**Why not a spec-level ``extras_descriptions={...}`` mapping.** Keying prose by
input name puts the name in two places and lets them disagree the day one is
renamed — the desync
[`FieldMarking`][rest_framework_services.types.field_marking.FieldMarking]'s own
docstring gives as the reason markings live on the field. A marker in the
``Annotated`` position cannot drift from what it describes, because it is
attached to it.

The marker is **advertisement-only**: it changes nothing about delivery,
requiredness, the kwargs pool, or the ``SPREAD_AUTHOR_WINS`` precedence. It
composes with ``InputRequired`` in one ``Annotated``, in either order, and with
foreign metadata from other libraries. Two of them on one input is refused, and
so is one beside ``NotClientInput`` — a key dropped from the schema has nowhere
for the text to land, so the declaration would decide nothing. Both are read by
[`read_input_description`][rest_framework_services.types.read_input_description.read_input_description].
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class InputDescription:
    """Prose for one reflected input, published as the schema's ``description``.

    Named for the schema key it produces rather than for DRF's ``help_text``,
    which is the nearest neighbour: ``help_text`` is a ``Field`` kwarg carrying
    form rendering and browsable-API behaviour with it, and a reflected extras
    key has no field to carry any of that. Borrowing the word would promise
    behaviour this does not have. ``InputDescription`` instead sits with the
    ``Input*`` markers it composes with, and with the ``description=`` argument
    [`UrlKwarg`][rest_framework_services.types.url_kwarg.UrlKwarg] and
    [`QueryParam`][rest_framework_services.types.query_param.QueryParam] already
    take for exactly this — the same word for the same job, whether the input is
    declared on the callable or registered by an adapter.

    Unlike the other two markers this is **not** a singleton: it carries text, so
    each declaration is its own value. Two instances with the same text compare
    equal, which is what a frozen dataclass gives and what a reader would expect;
    identity is never what reads it.

    Attributes:
        text: The sentence published as ``description``. Blank or
            whitespace-only is refused at construction — a marker that says
            nothing is a declaration with no effect, and emitting
            ``"description": ""`` spends tokens on every listing to publish an
            absence.
    """

    text: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ImproperlyConfigured(
                "InputDescription() needs text: an empty description publishes an empty "
                "string to every caller instead of saying nothing. Drop the marker if "
                "there is nothing to say."
            )


__all__ = ["InputDescription"]
