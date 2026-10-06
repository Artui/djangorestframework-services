"""``can_present_nothing``: whether a spec's dispatch may present ``None``.

The truth table first, one test per row group, each named on the predicate where a
row is the only thing holding an operand of an ``and``. Then the two claims that make
the answer worth sharing: every ``True`` row really does come back ``None`` through
``dispatch_spec``, and ``spec_to_json_schema`` admits ``null`` exactly where the
predicate says.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import serializers

import rest_framework_services as pkg
from rest_framework_services.can_present_nothing import can_present_nothing
from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.jsonschema.spec_to_json_schema import spec_to_json_schema
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

RETRIEVE = SelectorKind.RETRIEVE
LIST = SelectorKind.LIST


class _Out(serializers.Serializer):
    id = serializers.IntegerField()


def _nothing(**_kwargs: Any) -> None:
    return None


def _rows(**_kwargs: Any) -> list[Any]:
    return []


def _selector(kind: SelectorKind, *, allow_none: bool = False) -> SelectorSpec[Any, Any]:
    return SelectorSpec(
        kind=kind,
        selector=_nothing if kind is RETRIEVE else _rows,
        allow_none=allow_none,
        output_serializer=_Out,
    )


def _re_read(kind: SelectorKind, *, selector: bool = True) -> SelectorSpec[Any, Any]:
    callable_ = (_nothing if kind is RETRIEVE else _rows) if selector else None
    return SelectorSpec(kind=kind, selector=callable_, output_serializer=_Out)


def _service(
    output: SelectorSpec[Any, Any] | None = None, *, allow_none: bool = False, many: bool = False
) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=_rows if many else _nothing,
        output_selector_spec=output,
        allow_none=allow_none,
        many=many,
        atomic=False,
    )


# ------------------------------------------------------------------ the table


_TABLE: list[Any] = [
    # SelectorSpec
    pytest.param(_selector(LIST), False, id="selector-list"),
    pytest.param(_selector(LIST, allow_none=True), False, id="selector-list-allow-none"),
    pytest.param(_selector(RETRIEVE), False, id="selector-retrieve"),
    pytest.param(_selector(RETRIEVE, allow_none=True), True, id="selector-retrieve-allow-none"),
    # ServiceSpec, no output declaration
    pytest.param(_service(), False, id="service-no-output"),
    pytest.param(_service(allow_none=True), True, id="service-no-output-allow-none"),
    # ServiceSpec, an output declaration with no re-read selector
    pytest.param(_service(_re_read(RETRIEVE, selector=False)), False, id="service-render-own"),
    pytest.param(
        _service(_re_read(RETRIEVE, selector=False), allow_none=True),
        True,
        id="service-render-own-allow-none",
    ),
    pytest.param(
        _service(_re_read(LIST, selector=False), allow_none=True),
        False,
        id="service-list-declared-but-no-re-read-allow-none",
    ),
    # ServiceSpec, a RETRIEVE re-read
    pytest.param(_service(_re_read(RETRIEVE)), True, id="service-retrieve-re-read"),
    pytest.param(
        _service(_re_read(RETRIEVE), allow_none=True),
        True,
        id="service-retrieve-re-read-allow-none",
    ),
    # ServiceSpec, a LIST re-read
    pytest.param(_service(_re_read(LIST)), False, id="service-list-re-read"),
    pytest.param(
        _service(_re_read(LIST), allow_none=True), False, id="service-list-re-read-allow-none"
    ),
    # ServiceSpec, a list payload
    pytest.param(_service(allow_none=True, many=True), False, id="service-many-allow-none"),
    pytest.param(
        _service(_re_read(RETRIEVE), allow_none=True, many=True),
        False,
        id="service-many-with-a-re-read",
    ),
]


@pytest.mark.parametrize(("spec", "expected"), _TABLE)
def test_the_truth_table(spec: Any, expected: bool) -> None:
    assert can_present_nothing(spec) is expected


def test_a_list_selector_never_presents_nothing() -> None:
    # Holds ``spec.kind is RETRIEVE``: without it ``allow_none`` alone answers.
    assert can_present_nothing(_selector(LIST, allow_none=True)) is False


def test_a_retrieve_selector_presents_nothing_only_under_allow_none() -> None:
    # Holds ``spec.allow_none``: without it every RETRIEVE answers ``True``.
    assert can_present_nothing(_selector(RETRIEVE)) is False
    assert can_present_nothing(_selector(RETRIEVE, allow_none=True)) is True


def test_a_service_with_no_output_spec_answers_its_own_allow_none() -> None:
    # Holds ``nested is not None``: without it ``None.selector`` raises.
    assert can_present_nothing(_service()) is False
    assert can_present_nothing(_service(allow_none=True)) is True


def test_a_re_read_spec_without_a_selector_is_no_re_read() -> None:
    # Holds ``nested.selector is not None``: without it the nested ``kind`` would
    # answer for a re-read that never runs.
    assert can_present_nothing(_service(_re_read(RETRIEVE, selector=False))) is False


def test_a_list_payload_never_presents_nothing() -> None:
    assert can_present_nothing(_service(_re_read(RETRIEVE), allow_none=True, many=True)) is False


def test_it_is_exported_from_the_package() -> None:
    assert pkg.can_present_nothing is can_present_nothing
    assert "can_present_nothing" in pkg.__all__


# ------------------------------------------------- it agrees with dispatch


@pytest.mark.django_db
@pytest.mark.parametrize(
    "spec", [pytest.param(row.values[0], id=row.id) for row in _TABLE if row.values[1]]
)
def test_every_true_row_really_presents_none(spec: Any) -> None:
    # Each callable in the table returns ``None`` (or an empty list), so a
    # ``True`` row is one dispatch can be seen to answer with ``None``.
    assert dispatch_spec(spec, user=None, params={}).value is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "spec",
    [
        _selector(LIST, allow_none=True),
        _service(_re_read(LIST), allow_none=True),
        _service(allow_none=True, many=True),
    ],
    ids=["selector-list", "service-list-re-read", "service-many"],
)
def test_a_list_result_is_presented_as_a_list(spec: Any) -> None:
    value = dispatch_spec(spec, user=None, params={}).value
    assert value is not None
    assert list(value) == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "spec",
    [
        SelectorSpec(kind=LIST, selector=_nothing, output_serializer=_Out),
        _service(SelectorSpec(kind=LIST, selector=_nothing, output_serializer=_Out)),
        ServiceSpec(service=_nothing, many=True, atomic=False),
    ],
    ids=["selector-list", "service-list-re-read", "service-many"],
)
def test_a_list_callable_returning_none_is_presented_unrefused(spec: Any) -> None:
    """The narrowing the docstring states, pinned so it stays true: a list result is
    *declared* never ``None``, and on these three paths a callable returning one
    anyway is the author's error that dispatch presents rather than refuses. The
    fourth, a ``LIST`` declaration with nothing to re-read, refuses it."""
    assert can_present_nothing(spec) is False
    assert dispatch_spec(spec, user=None, params={}).value is None


@pytest.mark.django_db
def test_a_retrieve_miss_without_allow_none_is_not_found_rather_than_none() -> None:
    assert dispatch_spec(_selector(RETRIEVE), user=None, params={}).kind == "not_found"


# --------------------------------------------- it agrees with the schema


# Every row, including a ``LIST`` output declaration with no ``selector``: that
# runs no re-read, so dispatch presents the service's own return as one value, and
# the schema states that one value rather than the declared ``kind``.
@pytest.mark.parametrize(("spec", "expected"), _TABLE)
def test_the_output_schema_admits_null_exactly_where_it_answers_true(
    spec: Any, expected: bool
) -> None:
    schema = spec_to_json_schema(spec, phase="output")
    if schema is None:
        # No output serializer, so no schema to widen; the predicate still answers
        # whether ``None`` may come back.
        assert isinstance(spec, ServiceSpec)
        assert spec.output_selector_spec is None
        return
    admits_null = isinstance(schema["type"], list) and "null" in schema["type"]
    assert admits_null is expected
