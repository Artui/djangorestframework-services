"""A service spec's output schema admits the ``None`` its dispatch can present.

Two ways a single-row ``ServiceSpec`` presents nothing: an output re-read whose
``selector`` finds no row (``dispatch_spec`` materializes it with ``.first()``), and a
service with no re-read that returns ``None`` and says so with ``allow_none=True``.
Each test checks the schema against the value dispatch actually returns, with a real
JSON Schema validator, so the schema and the dispatch cannot agree with each other by
both being wrong.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from jsonschema import Draft202012Validator
from rest_framework import serializers

from rest_framework_services import (
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    capability_manifest,
    dispatch_spec,
    spec_to_json_schema,
)

# ``atomic`` defaults on, and a transaction is a database access.
pytestmark = pytest.mark.django_db


class _TaskOut(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()


_ROW = {"id": 1, "title": "Write the report"}
_STRICT = {
    "type": "object",
    "properties": {"id": {"type": "integer"}, "title": {"type": "string"}},
    "required": ["id", "title"],
}
_NULLABLE = {**_STRICT, "type": ["object", "null"]}


def _archive_task() -> dict[str, Any]:
    return dict(_ROW)


def _touch_tasks() -> None:
    return None  # a service with nothing to return


def _live_task(*, result: Any) -> None:
    return None  # the output re-read: the row just archived is no longer live


def _same_task(*, result: Any) -> Any:
    return result


def _output(selector: Any = None, kind: SelectorKind = SelectorKind.RETRIEVE) -> Any:
    return SelectorSpec(kind=kind, selector=selector, output_serializer=_TaskOut)


def _present(spec: ServiceSpec[Any, Any, Any]) -> Any:
    return dispatch_spec(spec, user=AnonymousUser(), params={}).value


def _conforms(schema: dict[str, Any], value: Any) -> bool:
    return Draft202012Validator(schema).is_valid(value)


# ------------------------------------------------------------ the re-read half


class TestAnOutputReReadMayFindNoRow:
    """Needs no declaration: the re-read itself is what can come back empty."""

    def test_the_schema_admits_the_null_the_re_read_presents(self) -> None:
        spec = ServiceSpec(service=_archive_task, output_selector_spec=_output(_live_task))
        schema = spec_to_json_schema(spec, phase="output")
        value = _present(spec)
        assert value is None
        assert schema == _NULLABLE
        assert _conforms(schema, value)

    def test_a_row_the_re_read_finds_still_conforms(self) -> None:
        spec = ServiceSpec(service=_archive_task, output_selector_spec=_output(_same_task))
        schema = spec_to_json_schema(spec, phase="output")
        assert schema is not None
        assert _conforms(schema, _present(spec))

    def test_the_capability_manifest_states_the_same_schema(self) -> None:
        spec = ServiceSpec(service=_archive_task, output_selector_spec=_output(_live_task))
        registry = SpecRegistry()
        registry.register("archive_task", spec)
        (operation,) = capability_manifest(registry)["operations"]
        assert operation["output_schema"] == _NULLABLE
        assert operation["output_schema"] == spec_to_json_schema(spec, phase="output")

    def test_a_list_re_read_is_never_null(self) -> None:
        # A set, empty at worst. ``allow_none`` does not change that either.
        for allow_none in (False, True):
            spec = ServiceSpec(
                service=_archive_task,
                allow_none=allow_none,
                collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=list),
                output_selector_spec=_output(_same_task, kind=SelectorKind.LIST),
            )
            assert spec_to_json_schema(spec, phase="output") == {
                "type": "array",
                "items": _STRICT,
            }


# --------------------------------------------------------- the no-re-read half


class TestAServiceWithNoReReadDeclaresIt:
    def test_allow_none_admits_the_null_the_service_returns(self) -> None:
        spec = ServiceSpec(service=_touch_tasks, allow_none=True, output_selector_spec=_output())
        schema = spec_to_json_schema(spec, phase="output")
        value = _present(spec)
        assert value is None
        assert schema == _NULLABLE
        assert _conforms(schema, value)

    def test_allow_none_without_an_output_declaration_has_no_schema_to_widen(self) -> None:
        # No ``output_selector_spec`` at all means no declared output, so there is
        # no schema to widen; the flag is not an output declaration of its own.
        spec = ServiceSpec(service=_touch_tasks, allow_none=True)
        assert spec_to_json_schema(spec, phase="output") is None

    def test_without_it_the_schema_stays_strict(self) -> None:
        spec = ServiceSpec(service=_archive_task, output_selector_spec=_output())
        assert spec_to_json_schema(spec, phase="output") == _STRICT
        assert _conforms(_STRICT, _present(spec))

    def test_an_undeclared_none_is_still_presented(self) -> None:
        # The documented limit of this release: dispatch neither refuses nor
        # replaces a ``None`` the spec did not declare, so it is served against a
        # schema that does not admit it.
        spec = ServiceSpec(service=_touch_tasks, output_selector_spec=_output())
        assert _present(spec) is None
        assert spec_to_json_schema(spec, phase="output") == _STRICT

    def test_a_list_payload_is_never_null(self) -> None:
        spec = ServiceSpec(
            service=lambda *, data: [], many=True, allow_none=True, output_selector_spec=_output()
        )
        assert spec_to_json_schema(spec, phase="output") == {"type": "array", "items": _STRICT}

    def test_the_nested_allow_none_is_still_not_read(self) -> None:
        # The declaration is the service spec's own. The nested flag keeps the
        # meaning it has always had there, which is none.
        nested = SelectorSpec(
            kind=SelectorKind.RETRIEVE, allow_none=True, output_serializer=_TaskOut
        )
        spec = ServiceSpec(service=_touch_tasks, output_selector_spec=nested)
        assert spec_to_json_schema(spec, phase="output") == _STRICT


@pytest.mark.parametrize("allow_none", [False, True])
def test_allow_none_is_carried_on_the_spec(allow_none: bool) -> None:
    assert ServiceSpec(service=_touch_tasks, allow_none=allow_none).allow_none is allow_none


def test_allow_none_defaults_to_false() -> None:
    assert ServiceSpec(service=_touch_tasks).allow_none is False
