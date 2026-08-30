"""Unit tests for the ``InputDescription`` marker."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services import InputDescription


def test_carries_its_text() -> None:
    assert InputDescription("The owning project.").text == "The owning project."


def test_is_not_a_singleton_and_compares_by_value() -> None:
    # Unlike ``InputRequired`` / ``NotClientInput`` this carries per-field state,
    # so two declarations are distinct values that compare by their text.
    assert InputDescription("one") == InputDescription("one")
    assert InputDescription("one") != InputDescription("two")


def test_is_frozen() -> None:
    marker = InputDescription("The owning project.")
    with pytest.raises(AttributeError):
        marker.text = "something else"  # type: ignore[misc]


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_blank_text_is_refused_at_construction(blank: str) -> None:
    with pytest.raises(ImproperlyConfigured, match="needs text"):
        InputDescription(blank)
