"""Tests for the ``NoInput`` sentinel type."""

from __future__ import annotations

from rest_framework_services import NoInput


def test_no_input_is_a_class() -> None:
    assert isinstance(NoInput, type)


def test_no_input_can_be_used_as_type_argument() -> None:
    # The point of ``NoInput`` is to bind ``InputT`` on the delete service
    # protocol; subscript binding shouldn't error.
    from rest_framework_services import DeleteService

    Bound = DeleteService[NoInput, object, None, dict]  # noqa: N806
    assert Bound is not None
