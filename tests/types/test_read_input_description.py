"""Unit tests for ``read_input_description``."""

from __future__ import annotations

from typing import Annotated

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services import InputDescription, InputRequired, NotClientInput
from rest_framework_services.types.read_input_description import read_input_description


def test_a_plain_annotation_has_none() -> None:
    assert read_input_description(int) is None


def test_an_annotated_without_the_marker_has_none() -> None:
    assert read_input_description(Annotated[int, InputRequired]) is None


def test_reads_the_text() -> None:
    assert read_input_description(Annotated[int, InputDescription("Owning project.")]) == (
        "Owning project."
    )


@pytest.mark.parametrize("order", ["marker-first", "marker-last"])
def test_composes_with_input_required_in_either_order(order: str) -> None:
    described = InputDescription("Owning project.")
    annotation = (
        Annotated[int, described, InputRequired]
        if order == "marker-first"
        else Annotated[int, InputRequired, described]
    )
    assert read_input_description(annotation) == "Owning project."


def test_foreign_metadata_is_ignored() -> None:
    # ``Annotated`` is a shared channel; another library's marker is not ours.
    assert read_input_description(Annotated[int, "help text", object()]) is None


def test_two_descriptions_are_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="2 InputDescription markers"):
        read_input_description(Annotated[int, InputDescription("one"), InputDescription("two")])


def test_a_description_beside_not_client_input_is_refused() -> None:
    # The key is dropped from the schema, so the text has no caller to reach.
    with pytest.raises(ImproperlyConfigured, match="both described and NotClientInput"):
        read_input_description(Annotated[str, NotClientInput, InputDescription("Team role.")])
