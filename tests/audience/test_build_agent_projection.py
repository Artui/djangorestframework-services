"""Tests for reading agent markings off a serializer."""

from __future__ import annotations

import dataclasses

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers

from rest_framework_services.audience.build_agent_projection import build_agent_projection
from rest_framework_services.types.agent_field import AGENT, AgentField
from rest_framework_services.types.field_audience import FieldAudience

STATUSES = [("PENDING_REVIEW", "Awaiting review"), ("PAID", "Paid")]


class _Line(serializers.Serializer):
    sku = serializers.CharField(style={AGENT: AgentField.handle()})
    description = serializers.CharField()


class _Invoice(serializers.Serializer):
    id = serializers.UUIDField(style={AGENT: AgentField.handle("Invoice handle.")})
    etag = serializers.CharField(style={AGENT: AgentField.hidden()})
    number = serializers.CharField(style={AGENT: AgentField.label()})
    status = serializers.ChoiceField(choices=STATUSES)
    plain = serializers.ChoiceField(choices=["a", "b"])
    lines = _Line(many=True)


def test_reads_markings_labels_and_nesting() -> None:
    projection = build_agent_projection(_Invoice)

    assert projection.audience("id") is FieldAudience.HANDLE
    assert projection.audience("etag") is FieldAudience.HIDDEN
    assert projection.audience("number") is FieldAudience.LABEL
    assert projection.audience("status") is FieldAudience.CONTENT
    assert projection.label == "number"
    assert projection.fields["id"].description == "Invoice handle."
    # Only labels that differ from their value are collected.
    assert projection.choice_labels == {
        "status": {"PENDING_REVIEW": "Awaiting review", "PAID": "Paid"}
    }
    # ``many=True`` wraps the child in a ListSerializer; the child still projects.
    assert projection.nested["lines"].audience("sku") is FieldAudience.HANDLE
    assert not projection.is_empty()


def test_unmarked_serializer_projects_empty() -> None:
    class _Plain(serializers.Serializer):
        name = serializers.CharField()
        choice = serializers.ChoiceField(choices=["a", "b"])

    assert build_agent_projection(_Plain).is_empty()


def test_non_serializer_projects_empty() -> None:
    @dataclasses.dataclass
    class _Row:
        name: str

    assert build_agent_projection(_Row).is_empty()
    assert build_agent_projection(None).is_empty()


def test_marking_under_another_key_still_counts() -> None:
    """The key is a naming courtesy; an ``AgentField`` can only be deliberate."""

    class _Odd(serializers.Serializer):
        secret = serializers.CharField(style={"whatever": AgentField.hidden()})

    assert build_agent_projection(_Odd).audience("secret") is FieldAudience.HIDDEN


def test_non_agent_field_under_agent_key_raises() -> None:
    class _Draft(serializers.Serializer):
        id = serializers.CharField(style={AGENT: "handle"})

    with pytest.raises(ImproperlyConfigured, match="not an AgentField"):
        build_agent_projection(_Draft)


def test_two_labels_raise() -> None:
    class _Ambiguous(serializers.Serializer):
        number = serializers.CharField(style={AGENT: AgentField.label()})
        title = serializers.CharField(style={AGENT: AgentField.label()})

    with pytest.raises(ImproperlyConfigured, match="A record has one name"):
        build_agent_projection(_Ambiguous)


def test_empty_child_projection_is_not_recorded() -> None:
    class _Child(serializers.Serializer):
        x = serializers.IntegerField()

    class _Parent(serializers.Serializer):
        marked = serializers.CharField(style={AGENT: AgentField.hidden()})
        child = _Child()

    assert build_agent_projection(_Parent).nested == {}


def test_drf_s_own_style_keys_are_not_mistaken_for_markings() -> None:
    """``style`` is a shared bag; a scan of it must walk past everyone else's keys."""

    class _Styled(serializers.Serializer):
        body = serializers.CharField(
            style={"base_template": "textarea.html", "input_type": "textarea", "rows": 5}
        )

    projection = build_agent_projection(_Styled)

    assert projection.fields == {}
    assert projection.is_empty()
