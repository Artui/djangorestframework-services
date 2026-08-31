"""Schema-marker reflection: ``InputRequired`` / ``NotClientInput`` end to end.

The pairing that matters is checked in ``test_marked_key_keeps_protocol_conformance``:
a genuinely required ``TypedDict`` key would make the callable non-assignable to
its selector Protocol under PEP 692, which is the whole reason the markers exist.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from typing_extensions import TypedDict, Unpack

from rest_framework_services.jsonschema.spec_to_json_schema import spec_to_json_schema
from rest_framework_services.jsonschema.utils import callable_input_schema
from rest_framework_services.selectors.list_selector import ListSelector
from rest_framework_services.types.http_extras import HttpExtras
from rest_framework_services.types.input_description import InputDescription
from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.selector_spec import SelectorSpec


class _WidgetExtras(HttpExtras[Any], total=False):
    project_pk: Annotated[int, InputRequired]
    team_role: Annotated[str, NotClientInput]
    note: str


def _list_widgets(**extras: Unpack[_WidgetExtras]) -> list[Any]:
    """List widgets in a project."""
    return []


class _Seeded(TypedDict, total=False):
    user: Annotated[str, InputRequired]
    pk: Annotated[int, InputRequired]


def _seeded(**extras: Unpack[_Seeded]) -> None: ...


def test_marked_key_keeps_protocol_conformance() -> None:
    # The point of the marker: requiredness without the PEP 692 assignability
    # break a required TypedDict key would cause.
    selector: ListSelector[Any] = _list_widgets
    assert selector is _list_widgets


def test_input_required_key_lands_in_schema_required() -> None:
    schema = spec_to_json_schema(SelectorSpec(selector=_list_widgets, kind="list"), phase="input")
    assert schema is not None
    assert schema["required"] == ["project_pk"]


def test_not_client_input_key_is_absent_from_properties() -> None:
    schema = spec_to_json_schema(SelectorSpec(selector=_list_widgets, kind="list"), phase="input")
    assert schema is not None
    assert sorted(schema["properties"]) == ["note", "project_pk"]


def test_marked_key_keeps_its_underlying_type() -> None:
    schema = spec_to_json_schema(SelectorSpec(selector=_list_widgets, kind="list"), phase="input")
    assert schema is not None
    assert schema["properties"]["project_pk"] == {"type": "integer"}


def test_annotated_without_a_marker_is_still_typed() -> None:
    # Regression: ``Annotated`` used to fall through to an untyped ``{}``.
    def fn(*, count: Annotated[int, "how many"]) -> None: ...

    properties, required = callable_input_schema(fn)
    assert properties == {"count": {"type": "integer"}}
    assert required == []


def test_markers_apply_to_ordinary_parameters() -> None:
    def fn(
        *, pk: Annotated[int, InputRequired], secret: Annotated[str, NotClientInput]
    ) -> None: ...

    properties, required = callable_input_schema(fn)
    assert properties == {"pk": {"type": "integer"}}
    assert required == ["pk"]


def test_skipped_seeds_win_over_a_required_marker() -> None:
    # A transport seed is never caller input, so it must not be advertised as
    # required even when the consumer marked it.
    properties, required = callable_input_schema(_seeded, skip=frozenset({"user"}))
    assert properties == {"pk": {"type": "integer"}}
    assert required == ["pk"]


class _DescribedExtras(HttpExtras[Any], total=False):
    project_pk: Annotated[
        int, InputRequired, InputDescription("The project whose widgets to list.")
    ]
    note: Annotated[str, InputDescription("Free text stored with the run.")]
    plain: int


def _list_described(**extras: Unpack[_DescribedExtras]) -> list[Any]:
    return []


def test_a_description_reaches_a_reflected_unpack_key() -> None:
    # The one that matters: a key declared only through ``Unpack[TypedDict]``,
    # composed with ``InputRequired`` in the same ``Annotated``, arrives at the
    # caller described instead of as a bare typed property.
    schema = spec_to_json_schema(SelectorSpec(selector=_list_described, kind="list"), phase="input")
    assert schema is not None
    assert schema["properties"]["project_pk"] == {
        "type": "integer",
        "description": "The project whose widgets to list.",
    }
    assert schema["required"] == ["project_pk"]


def test_a_described_key_without_input_required_stays_optional() -> None:
    schema = spec_to_json_schema(SelectorSpec(selector=_list_described, kind="list"), phase="input")
    assert schema is not None
    assert schema["properties"]["note"] == {
        "type": "string",
        "description": "Free text stored with the run.",
    }
    assert "note" not in schema["required"]


def test_an_undescribed_key_carries_no_description() -> None:
    schema = spec_to_json_schema(SelectorSpec(selector=_list_described, kind="list"), phase="input")
    assert schema is not None
    assert schema["properties"]["plain"] == {"type": "integer"}


def test_a_description_reaches_an_ordinary_parameter() -> None:
    def fn(*, pk: Annotated[int, InputDescription("The widget id.")]) -> None: ...

    properties, required = callable_input_schema(fn)
    assert properties == {"pk": {"type": "integer", "description": "The widget id."}}
    assert required == []


def test_an_unresolvable_annotation_is_still_described() -> None:
    # The type falls back to ``{}`` — "any JSON value" — and the sentence is the
    # only thing left telling the caller what to send, so it must survive.
    def fn(
        *, payload: Annotated[Any, InputDescription("Anything the widget accepts.")]
    ) -> None: ...

    properties, _required = callable_input_schema(fn)
    assert properties == {"payload": {"description": "Anything the widget accepts."}}


def test_a_described_ordinary_parameter_beside_not_client_input_is_refused() -> None:
    def fn(
        *, secret: Annotated[str, NotClientInput, InputDescription("Resolved by spec.kwargs.")]
    ) -> None: ...

    with pytest.raises(ImproperlyConfigured, match="both described and NotClientInput"):
        callable_input_schema(fn)


class _ContradictoryExtras(TypedDict, total=False):
    team_role: Annotated[str, NotClientInput, InputDescription("Resolved by spec.kwargs.")]


def _contradictory(**extras: Unpack[_ContradictoryExtras]) -> None: ...


class _SeededDescribed(TypedDict, total=False):
    user: Annotated[str, InputDescription("The signed-in principal.")]
    pk: Annotated[int, InputDescription("The widget id.")]


def _seeded_described(**extras: Unpack[_SeededDescribed]) -> None: ...


def test_a_described_unpack_key_beside_not_client_input_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="both described and NotClientInput"):
        callable_input_schema(_contradictory)


def test_a_described_key_that_is_skipped_is_not_advertised() -> None:
    properties, _required = callable_input_schema(_seeded_described, skip=frozenset({"user"}))
    assert properties == {"pk": {"type": "integer", "description": "The widget id."}}
