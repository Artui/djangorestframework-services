"""Tests for the shared ``types`` helpers."""

from __future__ import annotations

from types import MappingProxyType

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services.types.utils import validate_metadata


class TestValidateMetadata:
    def test_none_is_allowed(self) -> None:
        validate_metadata(None, label="SelectorSpec")

    def test_a_dict_is_allowed(self) -> None:
        validate_metadata({"scope": "tenant"}, label="SelectorSpec")

    def test_any_mapping_is_allowed(self) -> None:
        validate_metadata(MappingProxyType({"scope": "tenant"}), label="SelectorSpec")

    def test_non_mapping_raises_with_the_label_and_the_type(self) -> None:
        with pytest.raises(ImproperlyConfigured) as excinfo:
            validate_metadata([("scope", "tenant")], label="ServiceSpec")
        message = str(excinfo.value)
        assert "ServiceSpec.metadata must be a mapping (or None); got list" in message
        assert "never reads its contents" in message
