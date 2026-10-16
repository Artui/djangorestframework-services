"""A ``SelectorSpec`` with no ``selector`` still describes and renders its output.

Such a spec is how a view that falls back to its ``get_queryset()`` or
``get_object()`` declares what it presents, and the output schema, the agent
projection and the agent rendering read only that declaration. None of them
runs the selector, so none of them needs one. Running it still does: dispatch
and registration refuse the same spec, after it has been described and
rendered too, so describing it never leaves a compiled spec behind that a
later registration would accept.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers

from rest_framework_services.audience.audience_projection_for_spec import (
    audience_projection_for_spec,
)
from rest_framework_services.audience.utils import described_output
from rest_framework_services.can_present_nothing import can_present_nothing
from rest_framework_services.dispatch.arender_for_audience import arender_for_audience
from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.dispatch.render_for_audience import render_for_audience
from rest_framework_services.jsonschema.spec_to_json_schema import spec_to_json_schema
from rest_framework_services.registry.spec_registry import SpecRegistry
from rest_framework_services.types.field_marking import MARKING, FieldMarking
from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

RETRIEVE = SelectorKind.RETRIEVE
LIST = SelectorKind.LIST


class _Card(serializers.Serializer):
    id = serializers.IntegerField(style={MARKING: FieldMarking.handle()})
    title = serializers.CharField()
    secret = serializers.CharField(style={MARKING: FieldMarking.hidden()})


ROW = {"id": 1, "title": "one", "secret": "s"}


def _read(**_: Any) -> Any:
    return ROW


def _no_selector(kind: SelectorKind, **kwargs: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(kind=kind, output_serializer=_Card, **kwargs)


def _with_selector(kind: SelectorKind, **kwargs: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(kind=kind, selector=_read, output_serializer=_Card, **kwargs)


@pytest.mark.parametrize("kind", [RETRIEVE, LIST])
@pytest.mark.parametrize("allow_none", [False, True])
def test_the_output_schema_is_the_one_a_selector_would_have(
    kind: SelectorKind, allow_none: bool
) -> None:
    described = spec_to_json_schema(_no_selector(kind, allow_none=allow_none), phase="output")

    assert described is not None
    assert described == spec_to_json_schema(
        _with_selector(kind, allow_none=allow_none), phase="output"
    )


@pytest.mark.parametrize(
    ("kind", "allow_none", "expected"),
    [(RETRIEVE, False, False), (RETRIEVE, True, True), (LIST, True, False)],
)
def test_whether_it_presents_nothing_is_read_from_its_declaration(
    kind: SelectorKind, allow_none: bool, expected: bool
) -> None:
    assert can_present_nothing(_no_selector(kind, allow_none=allow_none)) is expected


def test_its_agent_projection_is_the_one_a_selector_would_have() -> None:
    projection = audience_projection_for_spec(_no_selector(RETRIEVE))

    assert projection == audience_projection_for_spec(_with_selector(RETRIEVE))
    assert "secret" in projection.fields


def test_it_renders_one_row_for_an_agent() -> None:
    assert render_for_audience(_no_selector(RETRIEVE), ROW) == {"id": 1, "title": "one"}


def test_it_renders_every_row_of_a_list_for_an_agent() -> None:
    rendered = render_for_audience(_no_selector(LIST), [ROW, ROW], many=True)

    assert rendered == [{"id": 1, "title": "one"}, {"id": 1, "title": "one"}]


async def test_it_renders_for_an_agent_off_the_event_loop_too() -> None:
    assert await arender_for_audience(_no_selector(RETRIEVE), ROW) == {"id": 1, "title": "one"}


@pytest.mark.parametrize("kind", [RETRIEVE, LIST])
def test_described_and_rendered_it_is_still_refused_where_it_would_run(
    kind: SelectorKind,
) -> None:
    spec = _no_selector(kind)
    spec_to_json_schema(spec, phase="output")
    audience_projection_for_spec(spec)
    can_present_nothing(spec)

    with pytest.raises(ImproperlyConfigured, match="declares no selector"):
        SpecRegistry().register("cards", spec)
    with pytest.raises(ImproperlyConfigured, match="declares no selector"):
        dispatch_spec(spec, user=None, params={})


def test_a_spec_with_a_selector_is_compiled_as_it_is() -> None:
    """The selector is stood in for only where there is none.

    A ``reads`` entry naming a parameter its selector marks ``NotClientInput``
    is refused by the compile, and stays refused through the output's readers,
    which would describe the spec without reading its selector otherwise.
    """

    def hiding(*, tenant: Annotated[int, NotClientInput]) -> Any:
        return ROW

    spec = SelectorSpec(
        kind=RETRIEVE,
        selector=hiding,
        output_serializer=_Card,
        allow_none=True,
        reads=[QueryParam("tenant", "integer")],
    )

    with pytest.raises(ImproperlyConfigured, match="NotClientInput"):
        can_present_nothing(spec)
    with pytest.raises(ImproperlyConfigured, match="NotClientInput"):
        audience_projection_for_spec(spec)


def test_the_selector_stood_in_for_refuses_if_it_is_ever_run() -> None:
    """Nothing that describes the copy runs its selector; anything that did is refused."""
    described = described_output(_no_selector(RETRIEVE))

    with pytest.raises(ImproperlyConfigured, match="declares no selector"):
        described.selector()
