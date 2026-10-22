"""``SerializerPresenter`` - the kernel's Presenter over a DRF serializer."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, Final

from django.core.exceptions import ImproperlyConfigured
from django_service_specs.output.field_marking import FieldMarking
from django_service_specs.output.output import Output, OutputField
from django_service_specs.output.presentation_context import PresentationContext
from django_service_specs.output.presenter import Presenter
from rest_framework import serializers
from rest_framework.fields import empty as _drf_empty

from rest_framework_services.service_specs.base_serializer_context import base_serializer_context
from rest_framework_services.service_specs.renderable_serializer_class import (
    renderable_serializer_class,
)
from rest_framework_services.service_specs.utils import (
    fragment_keywords,
    output_view_layers,
    registered_fragment,
    serializer_context,
)
from rest_framework_services.types.field_marking import MARKING
from rest_framework_services.types.json_schema_registry import JsonSchemaRegistry

# How many times one serializer class may appear on the walk's path before the
# next appearance is truncated: drfs' schema walk allows the same, so a
# self-bounded declaration (a countdown nesting three levels below its root) is
# described as written, and an unbounded one stops rather than recursing until
# the process dies. A serializer instantiates its fields lazily, so an unbounded
# self-nesting declaration is legal to build and renders a finite value; only a
# walk of its declaration would never end.
_MAX_SERIALIZER_APPEARANCES: Final = 4

# The kernel's JSON type and format per DRF field class, first ``isinstance``
# hit wins, so a subclass precedes its base. It is drfs' own schema table less
# the formats the kernel does not name (``time``, ``uuid``, ``email``, ``uri``):
# those fields still render a string, and a format a kernel reader cannot act on
# is left off rather than refused.
_SCALARS: Final[tuple[tuple[type[serializers.Field], str, str | None], ...]] = (
    (serializers.BooleanField, "boolean", None),
    (serializers.IntegerField, "integer", None),
    (serializers.FloatField, "number", None),
    (serializers.DecimalField, "string", "decimal"),
    (serializers.DateTimeField, "string", "date-time"),
    (serializers.DateField, "string", "date"),
    (serializers.TimeField, "string", None),
    (serializers.UUIDField, "string", None),
    (serializers.CharField, "string", None),
    (serializers.DictField, "object", None),
)


@dataclasses.dataclass(frozen=True)
class _Shape:
    """What one DRF field declares, before it is named: the half ``OutputField`` shares."""

    type: str | None
    format: str | None = None
    fields: Output | None = None
    items: str | Output | None = None
    items_nullable: bool = False
    choices: tuple[tuple[Any, str], ...] | None = None


class SerializerPresenter(Presenter):
    """An operation's output declared as a DRF serializer, rendered through it.

        ``output()`` reads the serializer's fields into the kernel's
        [`Output`][django_service_specs.output.output.Output], in declaration order,
        the way drfs' own output schema and audience walk read them:

        - A ``write_only`` field is never rendered, so it is not declared.
        - **Type**: JSON's own, with ``decimal``, ``date-time`` and ``date`` as
          formats. A nested serializer is an object carrying its own ``Output``; a
          ``many=True`` serializer, and a ``ListField`` whose child is one, is an
          array of that ``Output``; a ``MultipleChoiceField`` is an array of its
          choices. A field DRF gives no type, a
          ``SerializerMethodField`` or a ``JSONField`` say, is declared ``any``,
          so a schema describes it with no ``type`` rather than as a string it may
          not be.
    - **Label**: the author's, when one was written. DRF derives ``Vat id`` from
          ``vat_id`` when none was, and that is ``None`` here, as it is no ``title``
          in drfs' schema.
        - **Choices**: every ``(value, display)`` pair a ``ChoiceField`` holds,
          typed by the values when they share a type.
        - **Presence**: ``always_present`` is true where DRF is bound to emit the
          key: a required field, one with a default, and one allowing ``null``. A
          read-only field with neither is dropped when DRF cannot read it.
          Under ``shaped=True``, no field at any depth is: the spec compiles a
          shape, an argument such as a ``fields`` selector that the serializer
          reads off the request, and a key is always present only if it is
          present under every value the caller may send. A drfs ``QueryParam``
          declares no choices, so the values are any string, and which keys
          each one drops is the serializer's to decide when it renders, so the
          keys present under all of them are none that can be named. The
          default, ``False``, is the presence above.
        - **Marking**: the ``FieldMarking`` found in the field's ``style``, under
          ``MARKING`` or any other key, and a value under ``MARKING`` that is not
          one refused, as drfs' walk reads it. Nested outputs carry their own.

        ``field_audiences`` is the spec's own output declaration, a field name to a
        ``FieldMarking``, layered over the serializer's markings **at the top level
        only**, as drfs' ``overrides=`` always has been. The layered marking is the
        compiled ``OutputField.marking``, which is the seat
        [`audience_projection_for_spec`][django_service_specs.dispatch.audience_projection_for_spec.audience_projection_for_spec]
        reads, so every engine projects the one declaration with no ``overrides=``,
        which stays a mount's. A name that is no output field is refused when the
        output is read: a hidden marking that silently applies to nothing is a field
        shown to an agent.

        ``presented(value, context)`` is what the kernel's ``present`` asks for:
        the serializer's ``.data`` for one value, under the serializer context
        derived from the call's ``PresentationContext``. The ``request`` and
        ``view`` the call registered as pool seeds, read from ``ambient``, are the
        serializer's own, and where a seed is absent or ``None``, the entry of
        that name in ``call_context``; a call that carries neither gets an
        offline request whose ``user`` is the principal, so a
        ``SerializerMethodField`` reading ``self.context["request"].user``
        renders for the principal the call ran for, and whose query string is
        the call's ``shapes``, so a serializer reading a ``fields`` selector off
        ``request.query_params`` finds the one the caller sent. A request the
        call carries is left as it is. A list result is presented row by row by the kernel's ``present``,
        each row with the same context, which is what a ``many=True`` render is.
        Over that, the output half of the view hooks a DRF edge adds to the
        result's context (``output_view_layers``), resolved with the value, then ``serializer_context``, the
        spec's own ``output_serializer_context``, each winning on a key it shares
        with what it layers over (see
        ``serializer_context`` in ``service_specs/utils.py``).
        The callable declares what it reads from the call's pool, ``request``,
        ``view``, ``user`` or a registered seed, and the value being presented
        under ``value_keyword``: ``result`` for a service's output, ``instance``
        for a retrieve. **It is asked once per value presented**, so for a list
        or a ``many`` service's output once per row, and no keyword names the
        row then (``value_keyword`` is ``None``), nor is the view's hook handed
        it: the kernel presents a set row by row, and the whole set a callable
        may declare, a list's ``page`` or a ``many`` output's ``result``, is
        never in hand here.

        ``present(value)`` is the context-free form, and delegates to
        ``presented(value, None)``: a ``None`` context is the serializer context
        drfs describes a serializer with (``request``, ``view`` and ``format``
        present, each ``None``), so a ``get_fields`` reading
        ``self.context["request"]`` neither raises there nor sees a principal. It
        is what a ``DispatchResult`` a transport built by hand is presented with,
        and with no call to resolve it against, ``serializer_context`` is not
        asked (test_present_with_no_call_runs_no_callable).

        A dataclass type is read and rendered through the ``DataclassSerializer``
        drfs renders it with, and a ``BaseSerializer`` that is not a ``Serializer``
        declares no fields, so its output is empty while it still presents.
    """

    def __init__(
        self,
        serializer_class: type,
        *,
        field_audiences: Mapping[str, FieldMarking] | None = None,
        serializer_context: Callable[..., Mapping[str, Any]] | None = None,
        value_keyword: str | None = "result",
        shaped: bool = False,
        json_schema_registry: JsonSchemaRegistry | None = None,
    ) -> None:
        # Resolved at construction but never instantiated: specs are built at
        # import time, and a ``ModelSerializer`` reads the app registry when its
        # fields are built.
        if not isinstance(serializer_class, type):
            raise ImproperlyConfigured(_REFUSED.format(declared=serializer_class))
        resolved = renderable_serializer_class(serializer_class)
        if not issubclass(resolved, serializers.BaseSerializer):
            raise ImproperlyConfigured(_REFUSED.format(declared=serializer_class))
        for name, marking in (field_audiences or {}).items():
            if not isinstance(marking, FieldMarking):
                raise ImproperlyConfigured(
                    f"SerializerPresenter: field_audiences[{name!r}] is {marking!r}, not a "
                    "FieldMarking. Use FieldMarking.handle() / .hidden() / .label()."
                )
        self.serializer_class = resolved
        self.field_audiences: Mapping[str, FieldMarking] = MappingProxyType(
            dict(field_audiences or {})
        )
        self.serializer_context = serializer_context
        self.value_keyword = value_keyword
        self.shaped = shaped
        # The spec's, whose ``fields`` rules declare the fields they match
        # (test_a_compiled_spec_s_output_fields_take_its_rules).
        self.json_schema_registry = json_schema_registry

    def output(self) -> Output:
        """The serializer's fields as an Output, with ``field_audiences`` layered on. Never queries."""
        serializer = self.serializer_class(context=_context())
        # A ``BaseSerializer`` declaring no fields outputs none, and still meets
        # the check below: a marking it has no field to carry would otherwise be
        # dropped, and the key it hides presented.
        output = (
            _output(serializer, path=(), registry=self.json_schema_registry)
            if isinstance(serializer, serializers.Serializer)
            else Output()
        )
        if self.shaped:
            output = _none_always_present(output)
        unknown = sorted(set(self.field_audiences) - set(output.names()))
        if unknown:
            raise ImproperlyConfigured(
                f"{self.serializer_class.__name__}: field_audiences names {unknown!r}, which "
                f"the serializer does not output. It outputs {list(output.names())!r}."
            )
        return Output(
            tuple(
                dataclasses.replace(field, marking=self.field_audiences[field.name])
                if field.name in self.field_audiences
                else field
                for field in output
            )
        )

    def present(self, value: Any) -> Any:
        """One value as the serializer's ``.data``, with no call to render for."""
        return self.presented(value, None)

    def presented(self, value: Any, context: PresentationContext | None) -> Any:
        """One value as the serializer's ``.data``, under the context of the call it belongs to.

        ``None`` is presented as ``None``: it is JSON's null, and a serializer
        handed none renders a row of blank fields indistinguishable from a real one.
        """
        if value is None:
            return None
        if context is None:
            return self.serializer_class(value, context=_context()).data
        return self.serializer_class(
            value,
            context=serializer_context(
                context.principal,
                context.ambient,
                None,
                # A presenter naming no value is presenting one row of a set,
                # which the view's hook, written for the whole set, is not handed.
                view_layers=output_view_layers(
                    context.ambient, value if self.value_keyword is not None else None
                ),
                provider=self.serializer_context,
                extras={self.value_keyword: value} if self.value_keyword is not None else None,
                call_context=context.call_context,
                shapes=context.shapes,
            ),
        ).data


_REFUSED: Final = (
    "SerializerPresenter takes a serializer class or a dataclass type; got {declared!r}."
)


def _context() -> dict[str, Any]:
    """drfs' description context: DRF's three keys, each ``None``, for no call."""
    return base_serializer_context(view=None, request=None)


def _output(
    serializer: serializers.Serializer,
    *,
    path: tuple[type, ...],
    registry: JsonSchemaRegistry | None = None,
) -> Output:
    path = (*path, type(serializer))
    return Output(
        tuple(
            _output_field(name, field, serializer=serializer, path=path, registry=registry)
            for name, field in serializer.fields.items()
            if not field.write_only
        )
    )


def _output_field(
    name: str,
    field: serializers.Field,
    *,
    serializer: serializers.Serializer,
    path: tuple[type, ...],
    registry: JsonSchemaRegistry | None = None,
) -> OutputField:
    """One rendered field as an ``OutputField``.

    A field a ``registry`` rule matches is declared by the rule's fragment, split
    by ``fragment_keywords``, before any built-in reading, as drfs'
    ``output_to_json_schema`` describes it. Its own ``allow_null``, an author's
    ``label`` and its marking stand beside the fragment, as drfs' walk writes
    the label over it
    (test_the_field_s_own_help_label_and_null_stand_beside_the_fragment).
    """
    fragment = registered_fragment(registry, field)
    if fragment is not None:
        declared = fragment_keywords(fragment, output=True)
        declared.update(
            nullable=field.allow_null or declared.get("nullable", False),
            label=_declared_label(name, field) or declared.get("label"),
            always_present=_always_rendered(field),
            marking=_marking(field, serializer=serializer, name=name),
        )
        return OutputField(name, declared.pop("type"), **declared)
    shape = _shape(field, path=path, registry=registry)
    return OutputField(
        name,
        shape.type or "any",
        format=shape.format,
        nullable=field.allow_null,
        label=_declared_label(name, field),
        choices=shape.choices,
        always_present=_always_rendered(field),
        marking=_marking(field, serializer=serializer, name=name),
        fields=shape.fields,
        items=shape.items,
        items_nullable=shape.items_nullable,
    )


def _none_always_present(output: Output) -> Output:
    """``output`` with no field at any depth ``always_present``: a shaped spec's output.

    Down both seats a nested output has, an object's ``fields`` and an array's
    ``items``; each is held by its own field of
    test_a_shaped_output_marks_no_key_present_at_any_depth: ``one`` (the
    first) and ``many`` and ``listed`` (the second).
    """
    return Output(
        tuple(
            dataclasses.replace(
                field,
                always_present=False,
                fields=None if field.fields is None else _none_always_present(field.fields),
                items=(
                    _none_always_present(field.items)
                    if isinstance(field.items, Output)
                    else field.items
                ),
            )
            for field in output
        )
    )


def _shape(
    field: serializers.Field, *, path: tuple[type, ...], registry: JsonSchemaRegistry | None = None
) -> _Shape:
    """One field's type, and what it nests, with ``type=None`` where DRF names none.

    A ``ListField``'s element a ``registry`` rule matches is typed by the rule,
    and only typed (test_a_registered_list_element_is_typed_by_its_rule); a
    nested serializer's fields are matched as the top level's are
    (test_a_nested_serializer_s_fields_are_matched_too). A ``many=True``
    serializer's rows are walked whatever matches their class, as drfs' walk
    describes them (test_a_rule_wins_over_the_walk_of_a_nested_serializer).
    """
    # One arc for both classes, so each is held by its own field of
    # test_nested_serializers_and_lists_nest_their_output: ``rows`` and ``wrapped``.
    if isinstance(field, serializers.ListSerializer | serializers.ListField):
        # ``ListSerializer.child`` is typed ``Field`` by the stubs; both lists
        # hold one at runtime, ``ListField``'s defaulting to an untyped field.
        child: Any = field.child
        fragment = (
            registered_fragment(registry, child)
            if isinstance(field, serializers.ListField)
            else None
        )
        if fragment is not None:
            kind = fragment_keywords(fragment, output=True)["type"]
            return _Shape("array", items=None if kind == "any" else kind)
        element = _shape(child, path=path, registry=registry)
        items = element.fields if element.fields is not None else element.type
        # Only a declared element can be declared nullable: ``OutputField``
        # refuses ``items_nullable`` without ``items``. Each condition is held
        # by its own field of the same test: ``loose`` (the first) and
        # ``scores`` (the second).
        return _Shape("array", items=items, items_nullable=items is not None and child.allow_null)
    if isinstance(field, serializers.ManyRelatedField):
        return _Shape("array")
    if isinstance(field, serializers.Serializer):
        if path.count(type(field)) >= _MAX_SERIALIZER_APPEARANCES:
            return _Shape("object")
        return _Shape("object", fields=_output(field, path=path, registry=registry))
    if isinstance(field, serializers.MultipleChoiceField):
        # Before ``ChoiceField``, which it subclasses: a set of the choices. A
        # list's choices are its element's, as the kernel's other adapters
        # declare them.
        choices = _choices(field)
        return _Shape("array", items=_choices_type(choices), choices=choices)
    if isinstance(field, serializers.ChoiceField):
        choices = _choices(field)
        json_type = _choices_type(choices)
        # The kernel lists no choices on an untyped value: a list of values
        # says what type they are, and a mixed set says none. Held by the
        # ``mixed`` and ``amount`` fields of
        # test_choices_are_value_display_pairs_typed_by_their_values.
        return _Shape(json_type, choices=choices if json_type else None)
    for cls, json_type, fmt in _SCALARS:
        if isinstance(field, cls):
            return _Shape(json_type, format=fmt)
    return _Shape(None)


def _choices(field: serializers.ChoiceField) -> tuple[tuple[Any, str], ...]:
    # ``str()`` for a lazy translation, read in the language active when the
    # output is read, as drfs' walk reads it.
    return tuple((value, str(display)) for value, display in field.choices.items())


def _choices_type(choices: tuple[tuple[Any, str], ...]) -> str | None:
    """The JSON type every non-null choice value shares, or ``None`` when they share none.

    ``bool`` before ``int``, which it subclasses: a boolean described as an
    integer is a false claim rather than a vague one.
    """
    types = {_json_type(value) for value, _ in choices if value is not None}
    return types.pop() if len(types) == 1 else None


def _json_type(value: Any) -> str | None:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def _declared_label(name: str, field: serializers.Field) -> str | None:
    """The field's ``label`` when its author wrote one, else ``None``.

    ``Field.bind`` derives a label from the name when none was written, and that
    says nothing a reader of the name does not have. Every field read here is
    bound, so a label is always there to compare.
    """
    label = str(field.label)
    return None if label == name.replace("_", " ").capitalize() else label


def _always_rendered(field: serializers.Field) -> bool:
    """Whether DRF is bound to emit this field's key.

    Mirrors ``Field.get_attribute``'s fallbacks: a default is substituted,
    ``allow_null`` yields ``None``, and a required field raises rather than
    skipping. Anything else can vanish from the payload.

    No branch for coverage to see, so each term is held by its own row of
    test_output_reads_order_types_formats_labels_and_presence: ``name`` (the
    first), ``active`` (the second) and ``maybe`` (the third).
    """
    return bool(field.required or field.default is not _drf_empty or field.allow_null)


def _marking(
    field: serializers.Field, *, serializer: serializers.Serializer, name: str
) -> FieldMarking | None:
    """The field's ``FieldMarking``, or ``None``.

    Matched on the value, so a marking filed under another key still counts; a
    value under ``MARKING`` that is not one is refused, because it is the shape a
    half-finished migration leaves (a bare ``"handle"``) and would otherwise do
    nothing at all. The guard is one branch to coverage, so each condition is
    held by its own field of test_markings_are_read_off_the_style_bag: ``b``
    (the first: no ``MARKING`` key to read) and ``a`` (the second: a marking
    under it).
    """
    style: dict[Any, Any] = field.style or {}
    if MARKING in style and not isinstance(style[MARKING], FieldMarking):
        raise ImproperlyConfigured(
            f"{type(serializer).__name__}.{name}: style[{MARKING!r}] is {style[MARKING]!r}, "
            "not a FieldMarking. Use FieldMarking.handle() / .hidden() / .label(); a bare "
            "value here is silently ignored."
        )
    return next((value for value in style.values() if isinstance(value, FieldMarking)), None)
