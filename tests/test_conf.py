"""Tests for the conf module."""

from __future__ import annotations

from django.test import override_settings

from rest_framework_services.conf import DEFAULTS, get_setting


def test_default_returned_when_user_setting_absent() -> None:
    assert get_setting("ATOMIC_BY_DEFAULT") == DEFAULTS["ATOMIC_BY_DEFAULT"]


def test_user_setting_overrides_default() -> None:
    with override_settings(REST_FRAMEWORK_SERVICES={"ATOMIC_BY_DEFAULT": False}):
        assert get_setting("ATOMIC_BY_DEFAULT") is False


def test_unknown_user_setting_falls_back_to_default() -> None:
    # If the user dict omits the key, the default is returned even when the
    # dict is otherwise non-empty.
    with override_settings(REST_FRAMEWORK_SERVICES={"OTHER": "x"}):
        assert get_setting("ATOMIC_BY_DEFAULT") is True
