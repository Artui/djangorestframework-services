"""Tests for reading agent markings off a serializer."""

from __future__ import annotations

import dataclasses

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers

from rest_framework_services.audience.build_audience_projection import build_audience_projection
from rest_framework_services.types.field_audience import FieldAudience
from rest_framework_services.types.field_marking import MARKING, FieldMarking

STATUSES = [("PENDING_REVIEW", "Awaiting review"), ("PAID", "Paid")]


class _Line(serializers.Serializer):
    sku = serializers.CharField(style={MARKING: FieldMarking.handle()})
    description = serializers.CharField()


class _Invoice(serializers.Serializer):
    id = serializers.UUIDField(style={MARKING: FieldMarking.handle("Invoice handle.")})
    etag = serializers.CharField(style={MARKING: FieldMarking.hidden()})
    number = serializers.CharField(style={MARKING: FieldMarking.label()})
    status = serializers.ChoiceField(choices=STATUSES)
    plain = serializers.ChoiceField(choices=["a", "b"])
    lines = _Line(many=True)


def test_reads_markings_labels_and_nesting() -> None:
    projection = build_audience_projection(_Invoice)

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

    assert build_audience_projection(_Plain).is_empty()


def test_non_serializer_projects_empty() -> None:
    @dataclasses.dataclass
    class _Row:
        name: str

    assert build_audience_projection(_Row).is_empty()
    assert build_audience_projection(None).is_empty()


def test_marking_under_another_key_still_counts() -> None:
    """The key is a naming courtesy; an ``FieldMarking`` can only be deliberate."""

    class _Odd(serializers.Serializer):
        secret = serializers.CharField(style={"whatever": FieldMarking.hidden()})

    assert build_audience_projection(_Odd).audience("secret") is FieldAudience.HIDDEN


def test_non_agent_field_under_agent_key_raises() -> None:
    class _Draft(serializers.Serializer):
        id = serializers.CharField(style={MARKING: "handle"})

    with pytest.raises(ImproperlyConfigured, match="not an FieldMarking"):
        build_audience_projection(_Draft)


def test_two_labels_raise() -> None:
    class _Ambiguous(serializers.Serializer):
        number = serializers.CharField(style={MARKING: FieldMarking.label()})
        title = serializers.CharField(style={MARKING: FieldMarking.label()})

    with pytest.raises(ImproperlyConfigured, match="A record has one name"):
        build_audience_projection(_Ambiguous)


def test_empty_child_projection_is_not_recorded() -> None:
    class _Child(serializers.Serializer):
        x = serializers.IntegerField()

    class _Parent(serializers.Serializer):
        marked = serializers.CharField(style={MARKING: FieldMarking.hidden()})
        child = _Child()

    assert build_audience_projection(_Parent).nested == {}


def test_drf_s_own_style_keys_are_not_mistaken_for_markings() -> None:
    """``style`` is a shared bag; a scan of it must walk past everyone else's keys."""

    class _Styled(serializers.Serializer):
        body = serializers.CharField(
            style={"base_template": "textarea.html", "input_type": "textarea", "rows": 5}
        )

    projection = build_audience_projection(_Styled)

    assert projection.fields == {}
    assert projection.is_empty()


def test_a_serializer_that_reads_context_can_still_be_projected() -> None:
    """The baseline context DRF always supplies over HTTP is supplied here too.

    Reading ``self.context["request"]`` unguarded is routine, because behind a
    view the key is always present. Building the projection without it raised
    ``KeyError`` in the one place a caller could not see or fix.
    """

    class _ContextReading(serializers.Serializer):
        def get_fields(self) -> dict[str, serializers.Field]:
            assert self.context["request"] is None
            return {"etag": serializers.CharField(style={MARKING: FieldMarking.hidden()})}

    assert build_audience_projection(_ContextReading).audience("etag") is FieldAudience.HIDDEN


def test_a_list_field_wrapping_a_serializer_projects_its_child() -> None:
    """``ListField(child=Serializer())`` nests as surely as ``many=True`` does."""

    class _Parent(serializers.Serializer):
        lines = serializers.ListField(child=_Line())

    assert (
        build_audience_projection(_Parent).nested["lines"].audience("sku") is FieldAudience.HANDLE
    )


def test_overrides_layer_over_the_serializers_own_markings() -> None:
    """One mount returning what its sibling hides, without a second serializer."""
    projection = build_audience_projection(_Invoice, overrides={"etag": FieldMarking()})

    assert projection.audience("etag") is FieldAudience.CONTENT
    # Everything the override did not name is untouched, including the two
    # halves an override cannot address at all.
    assert projection.audience("id") is FieldAudience.HANDLE
    assert projection.label == "number"
    assert projection.choice_labels["status"]["PAID"] == "Paid"
    assert projection.nested["lines"].audience("sku") is FieldAudience.HANDLE


def test_an_override_can_move_the_label() -> None:
    projection = build_audience_projection(
        _Invoice, overrides={"number": FieldMarking(), "id": FieldMarking.label()}
    )

    assert projection.label == "id"


def test_an_override_that_leaves_two_labels_raises() -> None:
    """The clash the serializer could not have: two markings from two places."""
    with pytest.raises(ImproperlyConfigured, match=r"lookup_invoice.*'id'.*'number'"):
        build_audience_projection(
            _Invoice, overrides={"id": FieldMarking.label()}, name="lookup_invoice"
        )


def test_an_override_clash_names_the_serializer_when_no_name_is_given() -> None:
    """A mount that did not identify itself still gets a locatable message."""
    with pytest.raises(ImproperlyConfigured, match=r"^_Invoice: "):
        build_audience_projection(_Invoice, overrides={"id": FieldMarking.label()})


def test_overrides_apply_to_a_spec_that_renders_through_no_serializer() -> None:
    """``None`` projects empty, and an override is still the caller's to make.

    A spec rendering a plain dataclass has nothing to mark up, but the mount's
    declaration is about the payload, not about the serializer.
    """
    projection = build_audience_projection(None, overrides={"secret": FieldMarking.hidden()})

    assert projection.audience("secret") is FieldAudience.HIDDEN


def test_a_marking_filed_under_the_previous_key_still_takes_effect() -> None:
    """The style key was renamed ``AGENT`` -> ``MARKING`` and its value
    ``"drf_agent"`` -> ``"drf_marking"``. Detection matches on the *value* being
    a ``FieldMarking``, never on the key, so a serializer written against the
    older constant keeps working -- which is what makes renaming the key safe to
    do without an alias, and is a claim the changelog makes out loud.
    """

    class _Old(serializers.Serializer):
        sku = serializers.CharField(style={"drf_agent": FieldMarking.handle()})

    projection = build_audience_projection(_Old)

    assert projection.fields["sku"].audience is FieldAudience.HANDLE
