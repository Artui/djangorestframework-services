"""``AudienceProjection`` — a serializer's field markings, resolved once."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from rest_framework_services.types.field_audience import FieldAudience
from rest_framework_services.types.field_marking import FieldMarking


@dataclass(frozen=True)
class AudienceProjection:
    """How one serializer's output is shaped for an agent audience.

    Derived from the serializer's
    [`FieldMarking`][rest_framework_services.types.field_marking.FieldMarking] markings
    plus its own ``ChoiceField`` definitions. Nothing here depends on the
    instance being rendered, so a consumer builds it **once at registration** and
    passes it to every render rather than re-deriving it per call.
    """

    fields: Mapping[str, FieldMarking] = field(default_factory=dict)
    """Every explicitly marked field, by name. Unmarked fields are absent."""

    label: str | None = None
    """The field naming this record for a human, if one is marked."""

    choice_labels: Mapping[str, Mapping[Any, str]] = field(default_factory=dict)
    """Per ``ChoiceField``, the ``{value: display}`` pairs whose display differs
    from the value. Empty for a field whose labels only repeat its constants."""

    nested: Mapping[str, AudienceProjection] = field(default_factory=dict)
    """Child projections, by field name, for nested and list serializers."""

    def is_empty(self) -> bool:
        """True when applying this projection would change nothing.

        The fast path: an unmarked serializer with no labelled choices anywhere
        should cost a caller one boolean rather than a full payload walk.
        """
        return not (
            self.fields
            or self.label
            or self.choice_labels
            or any(not child.is_empty() for child in self.nested.values())
        )

    def audience(self, name: str) -> FieldAudience:
        """The audience declared for ``name``, defaulting to ``CONTENT``."""
        marking = self.fields.get(name)
        return marking.audience if marking is not None else FieldAudience.CONTENT
