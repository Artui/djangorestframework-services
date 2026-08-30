"""``FieldMarking`` — per-field agent presentation, plus the ``MARKING`` style key.

The key is exported from the same module as the class it labels because the two
are meaningless apart: ``MARKING`` exists only to carry an ``FieldMarking``, and an
``FieldMarking`` is only ever found under it. Same reasoning as
``types/input_required.py``, which exports its marker and that marker's type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from rest_framework_services.types.field_audience import FieldAudience
from rest_framework_services.types.value_formatter import ValueFormatter

MARKING: Final = "drf_marking"
"""Key under which an :class:`FieldMarking` is declared in a DRF field's ``style``.

Namespaced rather than bare (``"agent"``) because ``style`` is a shared bag any
library may write to. The namespacing is courtesy, not correctness: readers
match on the *value* being an ``FieldMarking``, so a marking under another key
still takes effect and another library's data under ``MARKING`` is refused loudly
rather than silently misread.
"""


@dataclass(frozen=True, slots=True)
class FieldMarking:
    """How one serializer field is presented to an agent audience.

    Declared in DRF's per-field ``style`` bag, which is the only door
    ``Meta.extra_kwargs`` opens onto a field constructor — so a
    ``ModelSerializer`` keeps auto-generating its fields instead of being
    rewritten field by field:

    ```python
    class InvoiceSerializer(serializers.ModelSerializer):
        class Meta:
            model = Invoice
            fields = ["id", "number", "status", "etag"]
            extra_kwargs = {
                "id":     {"style": {MARKING: FieldMarking.handle("Invoice handle.")}},
                "etag":   {"style": {MARKING: FieldMarking.hidden()}},
                "number": {"style": {MARKING: FieldMarking.label()}},
            }
    ```

    The marking lives on the **field**, not in a list on ``Meta``. That is what
    lets it travel into nested serializers with no hoisting rule, and what stops
    a rename from silently desyncing it from a name the parent maintains.

    Invisible to the DRF view path: ``style`` is read only by DRF's
    ``HTMLFormRenderer``, and only for its own keys, so a REST response is
    byte-identical whether or not a serializer is marked up.
    """

    audience: FieldAudience = FieldAudience.CONTENT
    description: str | None = None
    """Audience-facing description, replacing ``help_text`` for this audience only.

    ``help_text`` is shared with the frontend and the browsable API, so it cannot
    say "opaque handle, never read this out". This can, without changing a word
    of what a human reader sees.
    """

    formatter: ValueFormatter | None = None
    """How this field's value is rendered for the agent, if not verbatim.

    A [`ValueFormatter`][rest_framework_services.types.value_formatter.ValueFormatter]
    transforms the value and declares the JSON type it produces, so the payload
    and the schema move together. Unset — the default — is the whole existing
    behaviour, unchanged down to the byte.

    **An explicit formatter wins over the choice substitution derived from a
    ``ChoiceField``**, which is a real collision: a status field can be both a
    choice and something an author wants spelled their own way. Only one
    transform can apply, and the one written by hand is the one that was asked
    for; a derived default losing to an explicit declaration is the ordinary
    direction. That precedence used to fall out of the order of an ``elif``.

    **``HANDLE`` suppresses it**, exactly as it suppresses choice substitution:
    a handle is another tool's input, and a formatted machine identifier is a
    broken one. Declaring both is honoured as ``HANDLE`` and the formatter never
    runs. A field a second tool takes as input therefore wants
    [`handle`][rest_framework_services.types.field_marking.FieldMarking.handle],
    or that tool receives a display string its own input schema rejects.
    """

    @classmethod
    def handle(cls, description: str | None = None) -> FieldMarking:
        """An opaque identifier: passed to other tools, never spoken to a user."""
        return cls(FieldAudience.HANDLE, description)

    @classmethod
    def hidden(cls) -> FieldMarking:
        """Plumbing: dropped from the projected payload and the projected schema."""
        return cls(FieldAudience.HIDDEN)

    @classmethod
    def label(cls, description: str | None = None) -> FieldMarking:
        """The field that names this record for a human."""
        return cls(FieldAudience.LABEL, description)

    @classmethod
    def formatted(cls, formatter: ValueFormatter, description: str | None = None) -> FieldMarking:
        """Ordinary content, rendered through ``formatter``.

        The generic constructor: any transform that declares what it produces.
        [`timestamp`][rest_framework_services.types.field_marking.FieldMarking.timestamp]
        is one of these with the formatter filled in, and a field that is both
        formatted and something else — a formatted label, say — is written as
        ``FieldMarking(FieldAudience.LABEL, formatter=...)``.
        """
        return cls(FieldAudience.CONTENT, description, formatter)

    @classmethod
    def timestamp(cls, fmt: str | None = None, description: str | None = None) -> FieldMarking:
        """A date-time read as a formatted local string rather than raw ISO-8601.

        ```python
        extra_kwargs = {"due_at": {"style": {MARKING: FieldMarking.timestamp()}}}
        ```

        The zone is Django's active one and cannot be passed here; ``fmt`` is a
        ``strftime`` string and defaults to a day-first, 24-hour rendering.
        [`ValueFormatter.timestamp`][rest_framework_services.types.value_formatter.ValueFormatter.timestamp]
        holds the transform and the reasoning behind both of those.
        """
        return cls(FieldAudience.CONTENT, description, ValueFormatter.timestamp(fmt))
