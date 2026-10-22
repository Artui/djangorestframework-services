"""What a spec's ``json_schema_registry`` enforces when the spec is dispatched off HTTP.

A keyword a kernel field spells is checked as a built-in field's would be; a
keyword left in ``json_schema_extra`` is advertised and not checked.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import serializers

from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from rest_framework_services.types.json_schema_registry import JsonSchemaRegistry
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

pytestmark = pytest.mark.django_db


class _Count(serializers.Field):
    """A field DRF checks nothing about, so every refusal below is the rule's."""

    def to_internal_value(self, data: Any) -> Any:
        return data

    def to_representation(self, value: Any) -> Any:
        return value


class _In(serializers.Serializer):
    count = _Count()


class Cents: ...


def _service(*, count: Any, **_kwargs: Any) -> Any:
    return count


def _select(*, total: Cents) -> Any:
    return total


_REGISTRY = JsonSchemaRegistry(
    fields=((_Count, {"type": "integer", "minimum": 0, "multipleOf": 2}),),
    python_types=((Cents, {"type": "integer"}),),
)
_SERVICE = ServiceSpec(service=_service, input_serializer=_In, json_schema_registry=_REGISTRY)
_SELECTOR = SelectorSpec(
    kind=SelectorKind.RETRIEVE, selector=_select, json_schema_registry=_REGISTRY
)


def _codes(spec: Any, params: dict[str, Any]) -> Any:
    with pytest.raises(InvalidArguments) as excinfo:
        dispatch_spec(spec, user=None, params=params)
    return excinfo.value.get_codes()


@pytest.mark.parametrize(
    ("params", "codes"),
    [
        pytest.param({"count": "x"}, {"count": ["invalid"]}, id="the-type"),
        pytest.param({"count": -1}, {"count": ["min_value"]}, id="the-bound"),
    ],
)
def test_a_keyword_a_field_spells_is_checked(params: dict[str, Any], codes: Any) -> None:
    assert _codes(_SERVICE, params) == codes


def test_a_keyword_left_in_json_schema_extra_is_not_checked() -> None:
    # 5 is no multiple of 2: ``multipleOf`` is advertised, never enforced.
    assert dispatch_spec(_SERVICE, user=None, params={"count": 5}).value == 5


def test_a_read_a_python_type_rule_types_is_checked_as_an_annotation_is() -> None:
    assert _codes(_SELECTOR, {"total": "x"}) == {"total": ["invalid"]}
    assert dispatch_spec(_SELECTOR, user=None, params={"total": 3}).value == 3
