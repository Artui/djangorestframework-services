"""Django settings used by the test suite."""

from __future__ import annotations

SECRET_KEY = "test-secret-key"

DEBUG = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "rest_framework_services",
    "tests.testapp",
]

# Skip migrations for the test app — pytest-django builds the schema from models.
MIGRATION_MODULES = {"testapp": None}

USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

ROOT_URLCONF = "tests.urls"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}
