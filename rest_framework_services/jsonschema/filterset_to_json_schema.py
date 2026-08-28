"""``filterset_to_json_schema`` — JSON Schema properties for a django-filter FilterSet."""

from __future__ import annotations

from typing import Any

from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)


def filterset_to_json_schema(
    filter_set_class: Any,
    *,
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
) -> dict[str, dict[str, Any]]:
    """Map a django-filter ``FilterSet`` class to JSON Schema properties.

    Returns a dict shaped like the ``"properties"`` key of a JSON Schema object, ready
    to merge into a spec's input schema — which is what
    [`spec_to_json_schema`][rest_framework_services.jsonschema.spec_to_json_schema.spec_to_json_schema]
    does for a ``SelectorSpec`` carrying a ``filter_set``. Every filter is optional: a
    filter narrows the queryset but is never required to call the operation, so no name
    is added to a ``required`` array.

    ``registry.filters`` rules are tried first, so a consumer can map a custom
    filter type or override a built-in; common filter classes get accurate
    mappings otherwise, and anything unrecognised falls back to ``{}`` (JSON
    Schema "any value") rather than breaking generation. Requires the
    ``[filter]`` extra (``django-filter``), raising a clear ``ImportError`` when
    it is absent — only ever when a ``filter_set`` is actually introspected.
    """
    module = _import_django_filters()
    base_filters: dict[str, Any] = dict(getattr(filter_set_class, "base_filters", {}))
    return {
        name: _annotated(_filter_to_schema(module, filter_obj, registry), name, filter_obj)
        for name, filter_obj in base_filters.items()
    }


def _annotated(schema: dict[str, Any], name: str, filter_obj: Any) -> dict[str, Any]:
    """Add the human half of a filter: what it is called, and what it matches."""
    label: Any = getattr(filter_obj, "label", None)
    if label:
        schema["title"] = str(label)
    description = _description_for(name, filter_obj)
    if description is not None:
        schema["description"] = description
    return schema


def _description_for(name: str, filter_obj: Any) -> str | None:
    """What this filter matches, when the argument's own name does not say.

    ``min_views`` is ``views__gte``, and a caller reading the schema sees a
    number called ``min_views`` -- not whether it is a floor, a ceiling or an
    exact match. The field name and the lookup are both already on the filter;
    they were simply never emitted, which is the reported complaint about an
    agent not knowing a project's vocabulary, stated concretely.

    Silent where the name already carries it: a filter called ``name`` matching
    ``name`` for equality has nothing to add, and an annotation restating the
    property is a cost with no reader. An author's own ``help_text`` always
    wins -- they know what the filter is for, and this only ever knew its shape.
    """
    extra: dict[str, Any] = getattr(filter_obj, "extra", {}) or {}
    help_text: Any = extra.get("help_text")
    if help_text:
        return str(help_text)
    field_name: Any = getattr(filter_obj, "field_name", None) or name
    lookup: Any = getattr(filter_obj, "lookup_expr", None) or "exact"
    if field_name == name and lookup == "exact":
        return None
    return f"Matches `{field_name}` with the `{lookup}` lookup."


def _import_django_filters() -> Any:
    # Function-local under the optional-dependency exemption: the core never
    # imports django-filter (``SelectorSpec.filter_set`` is applied by duck
    # typing), so introspection is the only place that needs it.
    try:
        import django_filters
    except ImportError as exc:
        raise ImportError(
            "filterset_to_json_schema requires the `django-filter` package. "
            'Install it with `pip install "djangorestframework-services[filter]"`.'
        ) from exc
    return django_filters


def _filter_to_schema(
    module: Any, filter_obj: Any, registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY
) -> dict[str, Any]:
    """Map a single filter instance to a JSON Schema fragment.

    ``registry.filters`` rules win first. After that order matters: subclass
    checks come before their base classes, falling back to the scalar mapping
    (and ultimately ``{}``) for unrecognised filters.
    """
    for rule_type, rule_schema in registry.filters:
        if isinstance(filter_obj, rule_type):
            return dict(rule_schema)
    if isinstance(filter_obj, module.BaseRangeFilter):
        scalar = _scalar_for(module, filter_obj)
        return {"type": "object", "properties": {"min": scalar, "max": dict(scalar)}}
    if isinstance(filter_obj, module.BaseInFilter):
        return {"type": "array", "items": _scalar_for(module, filter_obj)}
    if isinstance(filter_obj, module.MultipleChoiceFilter):
        return {"type": "array", "items": _choice_schema(filter_obj)}
    # ``ModelChoiceFilter`` subclasses ``ChoiceFilter``, so the FK-shaped
    # variant has to come first. The PK type isn't known without a DB
    # round-trip, so surface ``string`` and let the FilterSet coerce.
    if isinstance(filter_obj, module.ModelChoiceFilter):
        return {"type": "string"}
    if isinstance(filter_obj, module.ChoiceFilter):
        return _choice_schema(filter_obj)
    return _scalar_for(module, filter_obj)


def _scalar_for(module: Any, filter_obj: Any) -> dict[str, Any]:
    """Return the scalar JSON Schema for a filter's underlying type.

    Used directly for plain filters and as the ``items`` / ``min`` / ``max``
    shape for array- and range-style ones. Subclasses before bases.
    """
    if isinstance(filter_obj, module.BooleanFilter):
        return {"type": "boolean"}
    if isinstance(filter_obj, module.UUIDFilter):
        return {"type": "string", "format": "uuid"}
    if isinstance(filter_obj, module.DateTimeFilter):
        return {"type": "string", "format": "date-time"}
    if isinstance(filter_obj, module.DateFilter):
        return {"type": "string", "format": "date"}
    if isinstance(filter_obj, module.TimeFilter):
        return {"type": "string", "format": "time"}
    if isinstance(filter_obj, module.NumberFilter):
        return {"type": "number"}
    if isinstance(filter_obj, module.CharFilter):
        return {"type": "string"}
    return {}


def _choice_schema(filter_obj: Any) -> dict[str, Any]:
    """``enum`` when the labels add nothing, ``oneOf`` + ``title`` when they do.

    The rule, and the reasoning, are the serializer path's — see
    [`_choice_schema`][rest_framework_services.jsonschema.utils] there. This
    path kept only the values, so one package described the same constants two
    ways depending on which side of a spec they arrived on: a status called
    ``"r"`` came with ``"Red"`` from a serializer and bare from a FilterSet, and
    only the second is what an agent-facing schema was built from.

    Falls back to ``{"type": "string"}`` when ``extra["choices"]`` isn't present
    — some custom subclasses defer choice resolution.
    """
    extra: dict[str, Any] = getattr(filter_obj, "extra", {}) or {}
    choices: Any = extra.get("choices")
    if not choices:
        return {"type": "string"}
    pairs = [_choice_pair(choice) for choice in choices]
    if all(str(label) == str(value) for value, label in pairs):
        return {"enum": [value for value, _ in pairs]}
    return {"oneOf": [{"const": value, "title": str(label)} for value, label in pairs]}


def _choice_pair(choice: Any) -> tuple[Any, Any]:
    """One django-filter choice as ``(value, label)``.

    Choices arrive as ``[(value, label), ...]``, as bare values, and — for a
    grouped select — as ``(group_name, [(value, label), ...])``. The group form
    is not flattened here, and never was: it would need the schema to describe
    members this function does not walk. Its label is dropped rather than
    stringified, so the output stays the same shape it has always been rather
    than gaining a title made out of a repr.
    """
    if not isinstance(choice, tuple | list):
        return (choice, choice)
    value = choice[0]
    label = choice[1] if len(choice) >= 2 else value
    return (value, value if isinstance(label, tuple | list) else label)
