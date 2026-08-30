"""``ValueFormatter`` — a declared value transform, and the schema it produces."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.dateparse import parse_datetime

_JSON_TYPES: Final = ("string", "number", "integer", "boolean")
"""What a formatter may declare it produces.

Scalars only, and the omission of ``array`` / ``object`` is deliberate. A
container is described by the schema of its members, and ``schema`` cannot carry
member schemas by design (see below) — so a formatter producing one could only
ever advertise ``{"type": "object"}``, which is true and tells a reader nothing.
The four transforms this exists for — a formatted timestamp, money with its
currency, a duration, a quantity with its unit — all produce scalars.
"""

_TIMESTAMP_FORMAT: Final = "%d %b %Y %H:%M"
"""Day-month-year, so no reader has to guess which of the first two numbers is
the month. ``strftime`` codes rather than Django's own format characters: the
value reaching a formatter is whatever DRF rendered, which is usually a string,
so the stdlib parse-and-format pair is the one that round-trips it.
"""

_EXAMPLE_MOMENT: Final = datetime(2026, 1, 31, 14, 5, 9)
"""The instant every ``timestamp()`` example is rendered from.

A day past the twelfth and an hour past noon, so an example distinguishes
day-first from month-first and 24-hour from 12-hour. Naive on purpose: an
example is a shape, and a zone in it would read as a promise about which zone
the field is rendered in, which is exactly what this feature refuses to claim.
"""


@dataclass(frozen=True, slots=True)
class ValueFormatter:
    """One field's value, transformed for an agent audience, plus what that yields.

    Attached to a
    [`FieldMarking`][rest_framework_services.types.field_marking.FieldMarking] and
    applied by the same walk that drops hidden fields and speaks choice labels,
    on both sides at once: the payload gets ``render``'s result and the schema
    gets ``produces`` and ``schema``, from this one declaration.

    **This is generic on purpose.** The request that produced it was for
    formatted local timestamps, and taking that as filed would have left two
    hard-coded, type-specific value transforms where there was one. Money with
    its currency, a duration, a percentage and a quantity with its unit are all
    visible from this same spot; adding them one at a time is how a small
    marking type becomes a switch statement.
    [`timestamp`][rest_framework_services.types.value_formatter.ValueFormatter.timestamp]
    is a named constructor over the mechanism rather than a branch inside it.

    ```python
    money = ValueFormatter(
        lambda amount: f"EUR {amount}",
        produces="string",
        schema={"examples": ["EUR 1240.00"]},
    )
    ```

    **The declaration carries what it produces, and the framework writes that
    into the schema.** Choice substitution cannot lie because both sides are
    derived from the same ``ChoiceField``; a caller-supplied ``render`` can, so
    the type is declared next to it rather than inferred or left to a fragment.
    ``schema`` merges *over* the written type for ``description`` / ``examples``
    / ``format`` and is refused if it names ``type`` — a formatter that could
    contradict its own advertisement would put the schema/payload divergence
    this whole layer exists to prevent back inside a single declaration.

    Naming the type without describing the string it produces was the other
    rejected shape: what a formatted field looks like is most of what makes it
    discoverable, so both halves are here.

    Nothing checks ``render``'s *result* at render time. ``produces`` is a
    promise the author makes once, not a per-value assertion — the guarantee is
    that a renderer cannot advertise one thing and declare another, not that a
    misdeclared renderer is caught mid-call.
    """

    render: Callable[[Any], Any]
    """The transform, applied to the value DRF rendered.

    Called with a JSON-ready value, not a model attribute: the projection runs
    after ``render_spec_output``, so a ``DateTimeField`` arrives as its ISO-8601
    string and a ``DecimalField`` as whatever DRF's ``COERCE_DECIMAL_TO_STRING``
    made of it. Never called with ``None`` — see
    [`apply`][rest_framework_services.types.value_formatter.ValueFormatter.apply].
    """

    produces: Literal["string", "number", "integer", "boolean"]
    """The JSON type ``render`` returns. Written into the schema by the framework."""

    schema: Mapping[str, Any] | None = None
    """Extra JSON Schema keywords, merged over the written type.

    For saying what the produced value *looks like* — ``description``,
    ``examples``, ``format``. May not contain ``type``.
    """

    def __post_init__(self) -> None:
        if self.produces not in _JSON_TYPES:
            raise ImproperlyConfigured(
                f"ValueFormatter(produces={self.produces!r}) is not a JSON type. "
                f"Use one of {', '.join(_JSON_TYPES)}."
            )
        if self.schema is not None and "type" in self.schema:
            raise ImproperlyConfigured(
                "ValueFormatter(schema=...) may not set 'type' — produces= is what "
                "declares it, so a formatter cannot contradict its own advertisement. "
                f"Set produces={self.schema['type']!r} instead."
            )

    def apply(self, value: Any) -> Any:
        """``render(value)``, except that ``None`` passes straight through.

        A null is the absence of the value the formatter formats, and every
        transform would otherwise have to re-implement the same guard — a
        nullable field is ordinary, and ``strftime`` on ``None`` raises. Doing
        it here also keeps the schema exactly as complete as an unformatted
        field's: the walk declares no nullability for either.
        """
        return None if value is None else self.render(value)

    def json_schema(self) -> dict[str, Any]:
        """The declared type, with ``schema`` merged over it.

        ``type`` is written *last* on purpose. ``__post_init__`` already refuses
        a fragment that names it, so this can never be the thing that decides —
        and that is the point: the guarantee is structural as well as validated,
        rather than resting on a check somebody may one day relax.
        """
        return {**(self.schema or {}), "type": self.produces}

    @classmethod
    def timestamp(cls, fmt: str | None = None) -> ValueFormatter:
        """A date-time as a formatted local string, with an example of the shape.

        ```python
        due_at = serializers.DateTimeField(
            style={MARKING: FieldMarking(formatter=ValueFormatter.timestamp())}
        )
        ```

        **The zone is Django's active one, and there is no way to pass another.**
        DRF's ``DateTimeField.to_representation`` calls ``enforce_timezone``,
        which reads ``django.utils.timezone.get_current_timezone()``, so the
        HTTP path already renders in whatever zone is active. Reading the same
        source makes the two transports agree by construction rather than by
        discipline, and it is what a per-tenant middleware calling
        ``timezone.activate()`` is already for. A worker activates the zone
        itself, as it must for the ORM anyway.

        **A callable zone is not merely unsupported, it is impossible.**
        [`build_audience_projection`][rest_framework_services.audience.build_audience_projection.build_audience_projection]
        is pure in the serializer class and built once, and the schema side is
        built with ``view=None, request=None`` by construction — a schema is
        described before any request exists to describe (``serializer_for_schema``
        says why at length). A per-request callable would therefore resolve
        differently on the two paths, which is the schema-versus-payload
        divergence the audience layer exists to prevent. The schema says a
        formatted string and never names a zone, and that is what keeps it
        honest.

        ``fmt`` is a ``strftime`` format string and defaults to a day-first,
        24-hour rendering. The example in the schema is rendered from it, so it
        cannot drift from what the field actually carries.

        A bare date is read as midnight, so a ``DateField`` can be formatted
        too — give it a ``fmt`` without a time, or the rendering invents one.
        Anything else passes through unchanged, for the same reason an
        unrecognised choice constant does: a stale or oddly-typed row should
        still be reported rather than take the call down. Declaring this on a
        field that never carries a date is a misdeclaration nothing here can
        detect.
        """

        resolved = fmt or _TIMESTAMP_FORMAT

        def render(value: Any) -> Any:
            return _local_datetime(value, resolved)

        return cls(render, "string", {"examples": [_EXAMPLE_MOMENT.strftime(resolved)]})


def _local_datetime(value: Any, fmt: str) -> Any:
    """``value`` as a ``fmt``-formatted string in the active zone, or unchanged.

    Both spellings DRF can hand over are accepted: the ISO-8601 string its
    default ``DATETIME_FORMAT`` produces, and the bare ``datetime`` a field
    declaring ``format=None`` returns. Django's own parser rather than
    ``datetime.fromisoformat`` because DRF writes ``Z`` for UTC, which
    ``fromisoformat`` could not read before Python 3.11 — and 3.10 is the floor.
    """
    moment = value if isinstance(value, datetime) else _parsed(value)
    if moment is None:
        return value
    # A naive datetime has no zone to convert from; ``localtime`` raises on one.
    # That is the ``USE_TZ = False`` project, whose datetimes are already local.
    if timezone.is_aware(moment):
        moment = timezone.localtime(moment)
    return moment.strftime(fmt)


def _parsed(value: Any) -> datetime | None:
    """``value`` read as a date-time string, or ``None`` if it is neither."""
    return parse_datetime(value) if isinstance(value, str) else None
