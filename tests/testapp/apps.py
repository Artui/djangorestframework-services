"""App config for the integration test app."""

from __future__ import annotations

from django.apps import AppConfig


class TestAppConfig(AppConfig):
    name = "tests.testapp"
    label = "testapp"
    default_auto_field = "django.db.models.BigAutoField"
