"""Internal walkers that turn DRF serializers / dataclasses into JSON Schema.

Nothing here imports ``drf-spectacular``: its ``@extend_schema_field`` /
``@extend_schema_serializer`` overrides are read off the
``_spectacular_annotation`` attribute the decorators stamp on classes, so the
generator works whether or not spectacular is installed. Keep it that way.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import inspect
import types
import typing
import uuid
from typing import Any, Literal, get_args, get_origin, get_type_hints

from rest_framework import serializers
from rest_framework.fields import empty as _drf_empty

from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)
from rest_framework_services.types.read_schema_markers import read_schema_markers
from rest_framework_services.types.typed_dict_input import typed_dict_input
from rest_framework_services.types.unpack_typed_dict import unpack_typed_dict

# Order matters: the walk takes the first ``isinstance`` hit, so more specific
# subclasses must precede broader ones (EmailField before CharField).
_DRF_FIELD_TO_SCHEMA: list[tuple[type[serializers.Field], dict[str, Any]]] = [
    (serializers.BooleanField, {"type": "boolean"}),
    (serializers.IntegerField, {"type": "integer"}),
    (serializers.FloatField, {"type": "number"}),
    (serializers.DecimalField, {"type": "string", "format": "decimal"}),
    (serializers.DateTimeField, {"type": "string", "format": "date-time"}),
    (serializers.DateField, {"type": "string", "format": "date"}),
    (serializers.TimeField, {"type": "string", "format": "time"}),
    (serializers.UUIDField, {"type": "string", "format": "uuid"}),
    (serializers.EmailField, {"type": "string", "format": "email"}),
    (serializers.URLField, {"type": "string", "format": "uri"}),
    (serializers.IPAddressField, {"type": "string"}),
    (serializers.JSONField, {}),
    (serializers.CharField, {"type": "string"}),
]


def field_to_schema(
    field: serializers.Field,
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
    *,
    for_output: bool = False,
) -> dict[str, Any]:
    """Convert a single DRF field into a JSON Schema fragment.

    ``registry.fields`` rules win over the built-in mapping; an unrecognised
    field falls back to ``{}`` rather than failing generation.

    ``for_output`` is threaded through nested serializers so a nested block
    describes the same direction as the block containing it.
    """
    default: dict[str, Any] = _field_to_schema_default(field, registry, for_output)
    return apply_field_override(field, default)


def _field_to_schema_default(
    field: serializers.Field, registry: JsonSchemaRegistry, for_output: bool
) -> dict[str, Any]:
    for rule_type, rule_schema in registry.fields:
        if isinstance(field, rule_type):
            return dict(rule_schema)
    if isinstance(field, serializers.ListField):
        child: serializers.Field | None = field.child
        item_schema: dict[str, Any] = (
            field_to_schema(child, registry, for_output=for_output) if child is not None else {}
        )
        return {"type": "array", "items": item_schema}
    if isinstance(field, serializers.ListSerializer):
        # The DRF stubs type ``ListSerializer.child`` as ``Field``; at runtime
        # it is always a ``Serializer``, hence the ignore.
        list_child: serializers.Serializer | None = field.child  # ty: ignore[invalid-assignment]
        if list_child is None:
            return {"type": "array", "items": {}}
        return {
            "type": "array",
            "items": serializer_to_schema(list_child, registry, for_output=for_output),
        }
    if isinstance(field, serializers.Serializer):
        return serializer_to_schema(field, registry, for_output=for_output)
    if isinstance(field, serializers.MultipleChoiceField):
        # Checked before ChoiceField, which it subclasses: this field accepts a
        # *set* of the choices, not one of them. FilePathField subclasses
        # ChoiceField too, but is single-valued, so the plain branch suits it.
        multiple: dict[str, Any] = {
            "type": "array",
            "items": _choice_schema(field, widen=False),
            "uniqueItems": True,
        }
        if not field.allow_empty:
            multiple["minItems"] = 1
        return multiple
    if isinstance(field, serializers.ChoiceField):
        return _choice_schema(field)
    for cls, schema in _DRF_FIELD_TO_SCHEMA:
        if isinstance(field, cls):
            return dict(schema)
    return {}


def _always_rendered(field: serializers.Field) -> bool:
    """Whether DRF is guaranteed to emit this field's key.

    Mirrors the fallbacks in ``Field.get_attribute``: a default is substituted,
    ``allow_null`` yields ``None``, and a required field raises rather than
    skipping. Anything else can vanish from the payload.
    """
    return bool(field.required or field.default is not _drf_empty or field.allow_null)


def _declared_label(name: str, field: serializers.Field) -> str | None:
    """The field's ``label`` when its author wrote one, else ``None``.

    Every bound DRF field has a label: ``Field.bind`` derives one from the field
    name when the author declared none. That derivation says nothing a reader
    of the property name does not already have -- ``"Vat id"`` beside
    ``vat_id`` -- so emitting it as ``title`` costs tokens to restate the key in
    worse English. An author's own label is the opposite: it is the only place
    the human phrasing for a field lives, and the choice path next door already
    carries labels for exactly that reason.
    """
    label: Any = field.label
    if label is None or str(label) == _derived_label(name):
        return None
    return str(label)


def _derived_label(name: str) -> str:
    """What ``Field.bind`` would have made of ``name`` on its own."""
    return name.replace("_", " ").capitalize()


def _choice_schema(field: serializers.ChoiceField, *, widen: bool = True) -> dict[str, Any]:
    """``enum`` when the labels add nothing, ``oneOf`` + ``title`` when they do.

    DRF holds ``{value: display}`` and only the values used to survive here. A
    consumer picking a constant does better when the human phrasing travels with
    it, and ``title`` is an annotation keyword — it constrains nothing, so the
    accepted set stays exactly the ``const``s. Labels that merely repeat their
    value are dropped: a title restating the constant teaches nothing.

    ``allow_blank`` / ``allow_null`` widen what DRF accepts without appearing in
    ``field.choices``, so they are declared rather than left implicit. This is
    narrower than general nullability — an enum-like schema claims an exhaustive
    value set, so omitting an accepted value is a concrete falsehood, where
    ``{"type": "string"}`` for a nullable ``CharField`` is merely incomplete.

    ``widen`` is off for the element schema of a ``MultipleChoiceField``: its
    ``allow_null`` and ``allow_empty`` describe the array, not a member, and the
    array schema declares them itself.
    """
    choices: dict[Any, Any] = dict(field.choices)
    extra: list[Any] = []
    if widen:
        # Only values the choices do not already declare. Django prepends an
        # empty choice to a ``FilePathField``, and a nullable field may list
        # ``None`` outright -- appending a second ``const`` for either makes
        # ``oneOf`` match twice, which is a *failure*, so the widening meant to
        # admit the value would reject it.
        if getattr(field, "allow_blank", False) and "" not in choices:
            extra.append("")
        if field.allow_null and None not in choices:
            extra.append(None)
    if all(str(label) == str(value) for value, label in choices.items()):
        return {"enum": [*choices, *extra]}
    return {
        "oneOf": [
            *({"const": value, "title": str(label)} for value, label in choices.items()),
            *({"const": value} for value in extra),
        ]
    }


def serializer_for_schema(serializer_cls: type[serializers.Serializer]) -> serializers.Serializer:
    """Instantiate a serializer for *description*, with the context render uses.

    Both schema entry points used to instantiate bare, so a serializer whose
    ``get_fields`` reads ``self.context["request"]`` -- routine, because over
    HTTP the key is always there -- raised ``KeyError`` from description while
    dispatch rendered it perfectly. The spec could be called and could not be
    described, which is the schema/payload divergence the audience layer exists
    to prevent.

    The baseline is the same one
    [`build_agent_projection`][rest_framework_services.audience.build_agent_projection.build_agent_projection]
    already synthesizes, and for the same reason.

    **The view and request are `None`, and cannot be otherwise.** A schema is
    built once, when a transport declares its tools -- before any request
    exists to describe. So a ``get_fields`` that *branches* on the view type
    still sees ``None`` here and describes the branch it takes for a caller
    with no view: reflection cannot report a field set that depends on who is
    asking, because at description time nobody is.
    """
    # Genuine circular import, deliberately local: ``dispatch`` re-exports
    # helpers that reach back into this package, so importing it at module
    # scope executes a half-built package. ``build_agent_projection`` records
    # the same constraint.
    from rest_framework_services.dispatch.base_serializer_context import base_serializer_context

    return serializer_cls(context=base_serializer_context(view=None, request=None))


def serializer_to_schema(
    serializer: serializers.Serializer,
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
    *,
    for_output: bool = False,
) -> dict[str, Any]:
    """Convert a DRF serializer instance into a JSON Schema object.

    ``for_output=False`` describes *input*: ``read_only`` fields are skipped and
    ``required`` mirrors ``field.required``.

    ``for_output=True`` describes what DRF actually renders. ``write_only``
    fields drop out instead, and every remaining field joins ``required``:
    ``required`` means the key is present, not that its value is non-null, and
    DRF emits every field's key either way. Skipping ``read_only`` here — as the
    input direction must — left an output schema silently missing its primary
    key, its ETag, and every ``SerializerMethodField``, none of which stopped
    being rendered.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, field in serializer.fields.items():
        if field.write_only if for_output else field.read_only:
            continue
        properties[name] = field_to_schema(field, registry, for_output=for_output)
        title = _declared_label(name, field)
        if title is not None:
            properties[name]["title"] = title
        if field.help_text:
            properties[name]["description"] = str(field.help_text)
        if _always_rendered(field) if for_output else field.required:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return apply_serializer_overrides(schema, type(serializer))


# Python types with a direct JSON counterpart, matched by identity. The string
# formats mirror ``_DRF_FIELD_TO_SCHEMA`` above, so a value described once as a
# serializer field and once as an annotation gets the same wire shape.
_PYTHON_TYPE_TO_SCHEMA: list[tuple[Any, dict[str, Any]]] = [
    (str, {"type": "string"}),
    (int, {"type": "integer"}),
    (float, {"type": "number"}),
    (bool, {"type": "boolean"}),
    (type(None), {"type": "null"}),
    (decimal.Decimal, {"type": "string", "format": "decimal"}),
    (datetime.datetime, {"type": "string", "format": "date-time"}),
    (datetime.date, {"type": "string", "format": "date"}),
    (datetime.time, {"type": "string", "format": "time"}),
    (uuid.UUID, {"type": "string", "format": "uuid"}),
]

# Origins whose members are unordered, so the array they describe is a set.
_UNIQUE_ITEM_ORIGINS = (set, frozenset)

# Containers that carry no member annotation. ``get_origin`` answers ``None``
# for a bare ``list`` where it answers ``list`` for ``list[int]``, and the
# container is worth publishing either way.
_BARE_CONTAINERS = (list, set, frozenset, dict)


def _python_type_to_schema(
    annotation: Any, registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY
) -> dict[str, Any]:
    """Best-effort mapping from a Python type annotation to JSON Schema.

    ``registry.python_types`` rules are matched by identity and win first. The
    ``Annotated[...]`` wrapper is stripped before anything else so markers do not
    push a typed annotation into the fallback; the markers themselves are read by
    the callers that care.

    Handled structurally, because a caller that declared this much deserves to
    have it published: the JSON scalars, ``None``, the stdlib scalars DRF
    already renders as formatted strings (``datetime`` / ``date`` / ``time`` /
    ``UUID`` / ``Decimal``), ``Literal[...]`` and ``Enum`` subclasses as an
    ``enum``, ``list`` / ``set`` / ``frozenset`` / ``dict``, and unions —
    including ``X | None`` — as ``anyOf``. Members are resolved recursively, so
    a registry rule reaches inside a container or a union.

    **What is left is ``{}``, and ``{}`` means "any JSON value".** A domain class
    (``Money``, a Django model), a ``Callable``, a bare ``Any`` and an annotation
    that could not be resolved all land there, and a caller reading the published
    schema cannot tell them apart from a value that genuinely is unconstrained.
    A type identity is exactly what ``registry.python_types`` takes, so register
    the ones that matter to you rather than letting them publish as anything.
    """
    annotation, _required, _hidden = read_schema_markers(annotation)
    for rule_type, rule_schema in registry.python_types:
        if annotation is rule_type:
            return dict(rule_schema)
    for python_type, schema in _PYTHON_TYPE_TO_SCHEMA:
        if annotation is python_type:
            return dict(schema)
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        # The member *values* are what crosses the wire; ``Enum`` iterates in
        # declaration order, which is the order a caller sees them declared in.
        return {"enum": [member.value for member in annotation]}
    return _parametrised_type_to_schema(annotation, registry)


def _parametrised_type_to_schema(annotation: Any, registry: JsonSchemaRegistry) -> dict[str, Any]:
    """The ``get_origin``-keyed half of ``_python_type_to_schema``."""
    origin: Any = get_origin(annotation)
    if origin is None:
        origin = next((bare for bare in _BARE_CONTAINERS if annotation is bare), None)
    args: tuple[Any, ...] = get_args(annotation)
    if origin is Literal:
        # ``Literal`` args are values, not types — they are the enum.
        return {"enum": list(args)}
    if origin in (typing.Union, types.UnionType):
        return {"anyOf": [_python_type_to_schema(arg, registry) for arg in args]}
    if origin is list:
        (item_type,) = args or (Any,)
        return {"type": "array", "items": _python_type_to_schema(item_type, registry)}
    if origin in _UNIQUE_ITEM_ORIGINS:
        (item_type,) = args or (Any,)
        return {
            "type": "array",
            "items": _python_type_to_schema(item_type, registry),
            "uniqueItems": True,
        }
    if origin is dict:
        # Only the value type is expressible: JSON object keys are strings, so a
        # non-string key annotation is a Python-side detail with nowhere to go.
        value_schema = _python_type_to_schema(args[1], registry) if args else {}
        if value_schema:
            return {"type": "object", "additionalProperties": value_schema}
        return {"type": "object"}
    return {}


def callable_input_schema(
    fn: Any,
    *,
    skip: frozenset[str] = frozenset(),
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
) -> tuple[dict[str, Any], list[str]]:
    """JSON Schema ``(properties, required)`` for a callable's declared inputs.

    An unannotated or unresolvable parameter still gets a property, typed
    ``{}`` — the caller needs to learn the parameter exists. A ``**kwargs``
    annotated ``Unpack[SomeTypedDict]`` is expanded key by key; a bare or
    ``Any`` ``**kwargs`` contributes nothing.

    ``skip`` drops transport seeds (``request`` / ``user`` / ``view``) from
    both ordinary parameters and expanded keys, and ``required`` never contains
    a skipped name. ``InputRequired`` / ``NotClientInput`` markers apply to
    both alike. Requiredness of an ordinary parameter is never inferred from
    its default: the framework may supply it from the kwargs pool rather than
    from caller input, so only the marker may declare it.
    """
    try:
        # ``include_extras`` is required: the schema markers ride in the
        # ``Annotated`` metadata, which the default would strip.
        hints = get_type_hints(fn, include_extras=True)
    except Exception:  # noqa: BLE001 — unresolvable forward refs → untyped, never fatal
        hints = {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in inspect.signature(fn).parameters.items():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            typed_dict = unpack_typed_dict(hints.get(name))
            if typed_dict is not None:
                td_props, td_required = _typed_dict_to_schema(
                    typed_dict, skip=skip, registry=registry
                )
                properties.update(td_props)
                required.extend(td_required)
            continue
        if name in skip or parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        if name not in hints:
            properties[name] = {}
            continue
        _underlying, marked_required, hidden = read_schema_markers(hints[name])
        if hidden:
            continue
        properties[name] = _python_type_to_schema(hints[name], registry)
        if marked_required:
            required.append(name)
    return properties, required


def _typed_dict_to_schema(
    typed_dict: type,
    *,
    skip: frozenset[str],
    registry: JsonSchemaRegistry,
) -> tuple[dict[str, Any], list[str]]:
    """``(properties, required)`` for the keys of an ``Unpack``-ed ``TypedDict``.

    A skipped key is dropped from ``required`` too, so a required-but-skipped
    key is never advertised as a caller input. ``InputRequired`` is the usable
    way to mark a key required here, because a genuinely required
    ``TypedDict`` key breaks the callable's Protocol conformance under PEP 692.
    """
    field_types, required_keys = typed_dict_input(typed_dict)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, hint in field_types.items():
        if name in skip:
            continue
        _underlying, marked_required, hidden = read_schema_markers(hint)
        if hidden:
            continue
        properties[name] = _python_type_to_schema(hint, registry)
        if name in required_keys or marked_required:
            required.append(name)
    return properties, required


def dataclass_to_schema(
    cls: type,
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
) -> dict[str, Any]:
    """Convert a plain ``@dataclass`` type into a JSON Schema object.

    ``cls`` must be a dataclass. A field is ``required`` when it has neither a
    default nor a default factory.
    """
    hints: dict[str, Any] = get_type_hints(cls)
    properties: dict[str, Any] = {}
    required: list[str] = []
    # ``cls`` cannot be annotated ``type[_typeshed.DataclassInstance]``: that
    # name has no runtime import and this signature is rendered into the docs,
    # so a TYPE_CHECKING-only name would break the strict docs build.
    for f in dataclasses.fields(cls):  # ty: ignore[invalid-argument-type]
        annotation: Any = hints.get(f.name, f.type)
        properties[f.name] = _python_type_to_schema(annotation, registry)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            required.append(f.name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def apply_serializer_overrides(schema: dict[str, Any], serializer_class: type) -> dict[str, Any]:
    """Layer ``@extend_schema_serializer`` metadata onto a JSON Schema object.

    Honours ``exclude_fields``, ``deprecate_fields`` and ``examples``;
    ``component_name`` / ``extensions`` are OpenAPI-componentisation concerns
    and ignored. A no-op when the class is not decorated.
    """
    annotation: Any = getattr(serializer_class, "_spectacular_annotation", None)
    if not isinstance(annotation, dict):
        return schema
    # Field-level annotations live on the same attribute name but carry a
    # ``field`` key; those are applied per-field by ``apply_field_override``.
    if "field" in annotation and "exclude_fields" not in annotation:
        return schema

    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    for excluded in annotation.get("exclude_fields") or ():
        properties.pop(excluded, None)
        if excluded in required:
            required.remove(excluded)
    if not required and "required" in schema:
        del schema["required"]

    for deprecated in annotation.get("deprecate_fields") or ():
        if deprecated in properties:
            properties[deprecated]["deprecated"] = True

    example_values: list[Any] = []
    for example in annotation.get("examples") or ():
        # An omitted ``OpenApiExample(value=...)`` defaults to
        # ``rest_framework.fields.empty``, a sentinel type rather than
        # ``None``, so both shapes have to be filtered.
        value: Any = getattr(example, "value", None)
        if value is not None and value is not _drf_empty:
            example_values.append(value)
    if example_values:
        schema["examples"] = example_values

    return schema


def apply_field_override(
    field: serializers.Field, default_schema: dict[str, Any]
) -> dict[str, Any]:
    """Replace a field's JSON Schema fragment when ``@extend_schema_field`` applied.

    A dict-shaped override is used verbatim. Non-dict forms (an
    ``OpenApiTypes`` enum, a serializer class) fall through to
    ``default_schema`` rather than fabricating output.
    """
    annotation: Any = getattr(field, "_spectacular_annotation", None)
    if not isinstance(annotation, dict):
        return default_schema
    override: Any = annotation.get("field")
    if isinstance(override, dict):
        # Copy: the annotation dict lives on the class and callers mutate ours.
        return dict(override)
    return default_schema
