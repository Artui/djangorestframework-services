"""``agent_projection_for_spec`` — where each spec kind keeps its serializer."""

from __future__ import annotations

from rest_framework import serializers

from rest_framework_services.audience.agent_projection_for_spec import agent_projection_for_spec
from rest_framework_services.types.agent_field import AGENT, AgentField
from rest_framework_services.types.field_audience import FieldAudience
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec


class _Marked(serializers.Serializer):
    etag = serializers.CharField(style={AGENT: AgentField.hidden()})


def test_a_selector_keeps_it_on_output_serializer() -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: [], output_serializer=_Marked)

    assert agent_projection_for_spec(spec).audience("etag") is FieldAudience.HIDDEN


def test_a_service_keeps_it_one_level_down() -> None:
    spec = ServiceSpec(
        service=lambda **_: None,
        atomic=False,
        output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Marked),
    )

    assert agent_projection_for_spec(spec).audience("etag") is FieldAudience.HIDDEN


def test_a_spec_that_renders_through_nothing_projects_empty() -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: [])

    assert agent_projection_for_spec(spec).is_empty()
