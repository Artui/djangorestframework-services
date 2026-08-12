"""Tests for the shared ``types`` helpers."""

from __future__ import annotations

from types import MappingProxyType

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services import RelationOrphan
from rest_framework_services.types.utils import validate_metadata, validate_relation_orphan


def _service(**_: object) -> None: ...


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


class TestValidateRelationOrphan:
    def test_every_member_is_accepted_as_a_member_or_a_string(self) -> None:
        for orphan in RelationOrphan:
            validate_relation_orphan(orphan, delete_service=None, label="ChildSpec")
            validate_relation_orphan(orphan.value, delete_service=None, label="ChildSpec")

    def test_an_unknown_value_raises(self) -> None:
        with pytest.raises(ValueError, match="ChildSpec.orphan must be one of"):
            validate_relation_orphan("archive", delete_service=None, label="ChildSpec")

    def test_the_default_composes_with_a_delete_service(self) -> None:
        # Every spec written before the field existed says ``AUTO``, so it is
        # not a second answer to the question the service already answers.
        validate_relation_orphan(RelationOrphan.AUTO, delete_service=_service, label="ChildSpec")

    def test_stating_it_beside_a_delete_service_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured) as excinfo:
            validate_relation_orphan("unlink", delete_service=_service, label="ChildSpec")
        message = str(excinfo.value)
        assert "ChildSpec: orphan='unlink' declared alongside delete_service" in message
        assert "would decide nothing" in message
        # The remedy names both directions.
        assert "Dispose of the row in the service" in message
        assert "drop the service" in message
