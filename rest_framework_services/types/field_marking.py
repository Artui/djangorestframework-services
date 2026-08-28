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
