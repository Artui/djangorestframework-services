"""Unit tests for ``read_schema_markers``."""

from __future__ import annotations

from typing import Annotated

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.read_schema_markers import read_schema_markers


def test_plain_annotation_passes_through_unmarked() -> None:
    assert read_schema_markers(int) == (int, False, False)


def test_reads_input_required() -> None:
    assert read_schema_markers(Annotated[int, InputRequired]) == (int, True, False)


def test_reads_not_client_input() -> None:
    assert read_schema_markers(Annotated[str, NotClientInput]) == (str, False, True)


def test_strips_annotated_carrying_only_foreign_metadata() -> None:
    # ``Annotated`` is a shared channel — another library's metadata must be
    # ignored, not rejected, and the underlying type still recovered.
    assert read_schema_markers(Annotated[int, "help text"]) == (int, False, False)


def test_reads_marker_alongside_foreign_metadata() -> None:
    assert read_schema_markers(Annotated[int, "help", InputRequired]) == (int, True, False)


def test_both_markers_raise() -> None:
    with pytest.raises(
        ImproperlyConfigured, match="cannot be both InputRequired and NotClientInput"
    ):
        read_schema_markers(Annotated[int, InputRequired, NotClientInput])


def test_markers_are_singletons() -> None:
    # Identity comparison is how the markers are detected, so a second
    # instantiation must return the same object.
    assert type(InputRequired)() is InputRequired
    assert type(NotClientInput)() is NotClientInput


def test_marker_reprs_are_the_public_names() -> None:
    assert repr(InputRequired) == "InputRequired"
    assert repr(NotClientInput) == "NotClientInput"
