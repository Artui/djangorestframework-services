"""Internal walkers that turn DRF serializers / dataclasses into JSON Schema.

Shared by the public ``*_to_json_schema`` functions in this package. These are
dependency-light — they touch only ``rest_framework.serializers`` and the
stdlib, so the generator never pulls ``drf-spectacular`` into the import path.
drf-spectacular ``@extend_schema_field`` / ``@extend_schema_serializer``
overrides are honoured purely by reading the ``_spectacular_annotation``
attribute the decorators stamp on classes (via :func:`getattr`), so the
override support is cost-free whether or not spectacular is installed.
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any, get_args, get_origin, get_type_hints

from rest_framework import serializers
from rest_framework.fields import empty as _drf_empty

from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)

# Map DRF field types to JSON Schema fragments. Order matters: more specific
# subclasses (BooleanField, IntegerField) must come before broader ones
# (CharField) because we walk this list and use the first ``isinstance`` hit.
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
) -> dict[str, Any]:
    """Convert a single DRF field into a JSON Schema fragment.

    Recurses into nested serializers and list fields. ``registry.fields`` rules
    are tried first (so a consumer can map a custom field type or override a
    built-in); unknown field types fall back to ``{}`` (any), so an exotic field
    never breaks generation. A drf-spectacular ``@extend_schema_field`` override
    is honoured as a final pass — when the field carries a
    ``_spectacular_annotation`` dict with a dict-shaped ``field`` value, that
    schema replaces the auto-derived one.
    """
    default: dict[str, Any] = _field_to_schema_default(field, registry)
    return apply_field_override(field, default)


def _field_to_schema_default(
    field: serializers.Field, registry: JsonSchemaRegistry
) -> dict[str, Any]:
    for rule_type, rule_schema in registry.fields:
        if isinstance(field, rule_type):
            return dict(rule_schema)
    if isinstance(field, serializers.ListField):
        child: serializers.Field | None = field.child
        item_schema: dict[str, Any] = field_to_schema(child, registry) if child is not None else {}
        return {"type": "array", "items": item_schema}
    if isinstance(field, serializers.ListSerializer):
        # The DRF stub types ``ListSerializer.child`` as ``Field``, but in
        # practice it's always a ``Serializer`` (otherwise ``ListField`` would
        # be the right choice). The runtime fallback handles the ``None`` case.
        list_child: serializers.Serializer | None = field.child  # ty: ignore[invalid-assignment]
        if list_child is None:
            return {"type": "array", "items": {}}
        return {"type": "array", "items": serializer_to_schema(list_child, registry)}
    if isinstance(field, serializers.Serializer):
        return serializer_to_schema(field, registry)
    if isinstance(field, serializers.ChoiceField):
        return {"enum": list(field.choices.keys())}
    for cls, schema in _DRF_FIELD_TO_SCHEMA:
        if isinstance(field, cls):
            return dict(schema)
    return {}


def serializer_to_schema(
    serializer: serializers.Serializer,
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
) -> dict[str, Any]:
    """Convert a DRF serializer instance into a JSON Schema object.

    Required fields (DRF ``required=True``) populate ``required``; everything
    else is optional. ``read_only`` fields are skipped (they're output, not
    input). Field-level help text becomes ``description``. ``registry`` rules
    flow into each field. Class-level ``@extend_schema_serializer`` overrides are
    layered on top — see :func:`apply_serializer_overrides`.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, field in serializer.fields.items():
        if field.read_only:
            continue
        properties[name] = field_to_schema(field, registry)
        if field.help_text:
            properties[name]["description"] = str(field.help_text)
        if field.required:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return apply_serializer_overrides(schema, type(serializer))


def _python_type_to_schema(
    annotation: Any, registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY
) -> dict[str, Any]:
    """Best-effort mapping from a Python type annotation to JSON Schema.

    Used when introspecting bare dataclasses (the convention drf-services
    services use for ``data`` when no ``input_serializer`` is configured).
    ``registry.python_types`` rules (matched by identity) win first; unknown
    types fall back to ``{}``.
    """
    for rule_type, rule_schema in registry.python_types:
        if annotation is rule_type:
            return dict(rule_schema)
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    origin: Any = get_origin(annotation)
    if origin is list:
        (item_type,) = get_args(annotation) or (Any,)
        return {"type": "array", "items": _python_type_to_schema(item_type, registry)}
    return {}


def callable_input_properties(
    fn: Any,
    *,
    skip: frozenset[str] = frozenset(),
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
) -> dict[str, Any]:
    """JSON Schema ``properties`` for a callable's declared parameters.

    Each parameter becomes one property: its annotation maps to a JSON type via
    :func:`_python_type_to_schema` (resolved through :func:`typing.get_type_hints`
    so string annotations under ``from __future__ import annotations`` work). An
    **un**-annotated (or unresolvable) parameter becomes a bare ``{}`` — the
    property is still surfaced by name, just untyped, which is the whole point:
    the caller learns the parameter *exists*.

    ``skip`` drops parameter names the caller does not supply — transport seeds
    (``request`` / ``user`` / …) and server-provided ``kwargs``. ``*args`` /
    ``**kwargs`` are always skipped.
    """
    try:
        hints = get_type_hints(fn)
    except Exception:  # noqa: BLE001 — unresolvable forward refs → untyped, never fatal
        hints = {}
    properties: dict[str, Any] = {}
    for name, parameter in inspect.signature(fn).parameters.items():
        if name in skip or parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        properties[name] = _python_type_to_schema(hints[name], registry) if name in hints else {}
    return properties


def dataclass_to_schema(
    cls: type,
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
) -> dict[str, Any]:
    """Convert a plain ``@dataclass`` type into a JSON Schema object.

    Field annotations are resolved via :func:`typing.get_type_hints` so
    dataclasses declared under ``from __future__ import annotations`` (string
    annotations) work. ``registry.python_types`` rules extend the type mapping.
    A field is ``required`` when it has neither a default nor a default factory.
    """
    hints: dict[str, Any] = get_type_hints(cls)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for f in dataclasses.fields(cls):
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

    drf-spectacular stores the decorator's keyword arguments on the serializer
    class as a ``_spectacular_annotation`` dict. We honour:

    - **exclude_fields** — drop the named properties and strip them from
      ``required``.
    - **deprecate_fields** — set ``"deprecated": true`` on the named properties.
    - **examples** — aggregate ``OpenApiExample.value`` into a JSON Schema
      ``examples`` array (placeholder / ``None`` values filtered out).

    ``component_name`` / ``extensions`` are OpenAPI-componentisation concerns
    and ignored. No-op when the class isn't decorated, so calling
    unconditionally keeps the integration cost-free for non-spectacular users.
    """
    annotation: Any = getattr(serializer_class, "_spectacular_annotation", None)
    if not isinstance(annotation, dict):
        return schema
    # Field-level annotations (from ``@extend_schema_field`` on a Field
    # subclass) live on the same attribute name but carry a ``field`` key.
    # Skip those — they're applied per-field via ``apply_field_override``.
    if "field" in annotation and "exclude_fields" not in annotation:
        return schema

    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    for excluded in annotation.get("exclude_fields") or ():
        properties.pop(excluded, None)
        if excluded in required:
            required.remove(excluded)
    if not required and "required" in schema:
        # Keep the schema lean — no empty ``required`` arrays.
        del schema["required"]

    for deprecated in annotation.get("deprecate_fields") or ():
        if deprecated in properties:
            properties[deprecated]["deprecated"] = True

    example_values: list[Any] = []
    for example in annotation.get("examples") or ():
        # ``OpenApiExample(value=...)`` defaults to ``rest_framework.fields.empty``
        # (a sentinel type, not ``None``) when the caller doesn't supply one.
        # Filter both shapes so placeholder examples don't pollute the schema.
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

    ``@extend_schema_field`` on a custom ``Field`` stores
    ``{"field": <schema-or-typeref>, ...}`` on the class. When ``field`` is a
    dict (the common form, e.g. ``{"type": "string", "format": "iban"}``) we
    use it verbatim — the OpenAPI field-level dialect is JSON-Schema-compatible
    for the kinds of overrides users typically apply. Non-dict forms (an
    ``OpenApiTypes`` enum, a serializer class) fall through to the default
    schema rather than fabricating output we can't reason about. Returns
    ``default_schema`` unchanged when no annotation is present.
    """
    annotation: Any = getattr(field, "_spectacular_annotation", None)
    if not isinstance(annotation, dict):
        return default_schema
    override: Any = annotation.get("field")
    if isinstance(override, dict):
        # Copy so callers can mutate without poisoning the class-level dict.
        return dict(override)
    return default_schema
