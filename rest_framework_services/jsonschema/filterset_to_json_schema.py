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

    Returns a dict shaped like the ``"properties"`` key of a JSON Schema object,
    ready to merge into a spec's input schema — which is what
    [`spec_to_json_schema`][rest_framework_services.jsonschema.spec_to_json_schema.spec_to_json_schema] does for a
    ``SelectorSpec`` carrying a ``filter_set``. Every filter is optional: a
    filter narrows the queryset but is never required to call the operation, so
    no name is added to a ``required`` array.

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
        name: _filter_to_schema(module, filter_obj, registry)
        for name, filter_obj in base_filters.items()
    }


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
    """Build an ``enum`` schema from a ``ChoiceFilter``-derived filter.

    Falls back to ``{"type": "string"}`` when ``extra["choices"]`` isn't present
    — some custom subclasses defer choice resolution.
    """
    extra: dict[str, Any] = getattr(filter_obj, "extra", {}) or {}
    choices: Any = extra.get("choices")
    if not choices:
        return {"type": "string"}
    # django-filter choices come as ``[(value, label), ...]``; keep the values.
    values: list[Any] = []
    for choice in choices:
        if isinstance(choice, tuple | list) and len(choice) >= 1:
            values.append(choice[0])
        else:
            values.append(choice)
    return {"enum": values}
