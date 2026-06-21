"""Tests for ``filterset_to_json_schema`` and its filter-type mapping."""

from __future__ import annotations

import sys
from typing import Any

import django_filters
import pytest

from rest_framework_services.jsonschema.filterset_to_json_schema import (
    _choice_schema,
    _filter_to_schema,
    _scalar_for,
    filterset_to_json_schema,
)
from rest_framework_services.types.json_schema_registry import DEFAULT_JSON_SCHEMA_REGISTRY
from tests.testapp.models import Author


class _NumberInFilter(django_filters.BaseInFilter, django_filters.NumberFilter): ...


class _NumberRangeFilter(django_filters.BaseRangeFilter, django_filters.NumberFilter): ...


class _CustomFilter(django_filters.Filter): ...


# --- filterset_to_json_schema (public) ---------------------------------------


def test_maps_declared_filters_to_properties() -> None:
    class _FS(django_filters.FilterSet):
        name = django_filters.CharFilter()
        age = django_filters.NumberFilter()
        active = django_filters.BooleanFilter()
        color = django_filters.ChoiceFilter(choices=[("r", "Red"), ("g", "Green")])

    assert filterset_to_json_schema(_FS) == {
        "name": {"type": "string"},
        "age": {"type": "number"},
        "active": {"type": "boolean"},
        "color": {"enum": ["r", "g"]},
    }


def test_empty_filterset_yields_empty_properties() -> None:
    class _FS(django_filters.FilterSet): ...

    assert filterset_to_json_schema(_FS) == {}


def test_missing_django_filter_raises_helpful_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FS(django_filters.FilterSet):
        name = django_filters.CharFilter()

    monkeypatch.setitem(sys.modules, "django_filters", None)
    with pytest.raises(ImportError, match="django-filter"):
        filterset_to_json_schema(_FS)


# --- _filter_to_schema (special shapes) --------------------------------------


def test_range_filter_is_min_max_object() -> None:
    assert _filter_to_schema(django_filters, _NumberRangeFilter()) == {
        "type": "object",
        "properties": {"min": {"type": "number"}, "max": {"type": "number"}},
    }


def test_in_filter_is_array_of_scalar() -> None:
    assert _filter_to_schema(django_filters, _NumberInFilter()) == {
        "type": "array",
        "items": {"type": "number"},
    }


def test_multiple_choice_is_array_of_enum() -> None:
    flt = django_filters.MultipleChoiceFilter(choices=[("a", "A"), ("b", "B")])
    assert _filter_to_schema(django_filters, flt) == {
        "type": "array",
        "items": {"enum": ["a", "b"]},
    }


def test_model_choice_is_string() -> None:
    flt = django_filters.ModelChoiceFilter(queryset=Author.objects.all())
    assert _filter_to_schema(django_filters, flt) == {"type": "string"}


def test_choice_filter_is_enum() -> None:
    flt = django_filters.ChoiceFilter(choices=[("x", "X")])
    assert _filter_to_schema(django_filters, flt) == {"enum": ["x"]}


def test_plain_filter_falls_through_to_scalar() -> None:
    assert _filter_to_schema(django_filters, django_filters.CharFilter()) == {"type": "string"}


# --- _scalar_for -------------------------------------------------------------


def test_scalar_for_each_known_type() -> None:
    assert _scalar_for(django_filters, django_filters.BooleanFilter()) == {"type": "boolean"}
    assert _scalar_for(django_filters, django_filters.UUIDFilter()) == {
        "type": "string",
        "format": "uuid",
    }
    assert _scalar_for(django_filters, django_filters.DateTimeFilter()) == {
        "type": "string",
        "format": "date-time",
    }
    assert _scalar_for(django_filters, django_filters.DateFilter()) == {
        "type": "string",
        "format": "date",
    }
    assert _scalar_for(django_filters, django_filters.TimeFilter()) == {
        "type": "string",
        "format": "time",
    }
    assert _scalar_for(django_filters, django_filters.NumberFilter()) == {"type": "number"}
    assert _scalar_for(django_filters, django_filters.CharFilter()) == {"type": "string"}


def test_scalar_for_unknown_filter_is_any() -> None:
    assert _scalar_for(django_filters, django_filters.Filter()) == {}


# --- _choice_schema ----------------------------------------------------------


def _with_choices(choices: Any) -> Any:
    return type("_F", (), {"extra": {"choices": choices}})()


def test_choice_schema_keeps_tuple_values() -> None:
    assert _choice_schema(_with_choices([("r", "Red"), ("g", "Green")])) == {"enum": ["r", "g"]}


def test_choice_schema_keeps_bare_values() -> None:
    assert _choice_schema(_with_choices(["x", "y"])) == {"enum": ["x", "y"]}


def test_choice_schema_without_choices_is_string() -> None:
    assert _choice_schema(_with_choices(None)) == {"type": "string"}


def test_choice_schema_without_extra_is_string() -> None:
    assert _choice_schema(object()) == {"type": "string"}


# --- registry rules (filters) ------------------------------------------------


def test_registry_filter_rule_maps_a_custom_filter() -> None:
    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
        filters=[(_CustomFilter, {"type": "string", "format": "custom"})]
    )
    assert _filter_to_schema(django_filters, _CustomFilter(), registry) == {
        "type": "string",
        "format": "custom",
    }


def test_registry_filter_rule_present_but_unmatched_falls_through() -> None:
    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(filters=[(_CustomFilter, {"x": 1})])
    assert _filter_to_schema(django_filters, django_filters.CharFilter(), registry) == {
        "type": "string"
    }


def test_registry_filter_rule_flows_through_filterset() -> None:
    class _FS(django_filters.FilterSet):
        ref = _CustomFilter()

    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
        filters=[(_CustomFilter, {"type": "string", "format": "custom"})]
    )
    assert filterset_to_json_schema(_FS, registry=registry) == {
        "ref": {"type": "string", "format": "custom"}
    }
