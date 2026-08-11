"""Unit tests for ``ChildSpec`` construction + validation."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services import ChildSpec
from tests.testapp.models import Item, Section


def _service(**_: Any) -> None: ...


class TestChildSpec:
    def test_defaults(self) -> None:
        spec = ChildSpec(model=Section, fk="catalog")
        assert spec.match_key == "pk"
        assert spec.mode == "replace"
        assert spec.m2m is None
        assert spec.children is None

    def test_merge_mode_allowed(self) -> None:
        assert ChildSpec(model=Section, fk="catalog", mode="merge").mode == "merge"

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="ChildSpec.mode must be one of"):
            ChildSpec(model=Section, fk="catalog", mode="upsert")


class TestServiceReplacesTheHelper:
    """A row service and the knobs configuring the helper call it replaces."""

    def test_create_service_with_children_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured) as excinfo:
            ChildSpec(
                model=Section,
                fk="catalog",
                create_service=_service,
                children={"items": ChildSpec(model=Item, fk="section")},
            )
        message = str(excinfo.value)
        assert "ChildSpec: create_service declared alongside children" in message
        assert "silently ignored" in message
        # The remedy names both directions, and says what is *not* affected.
        assert "drop the service" in message
        assert "fk / match_key / mode / scope / orphan handling" in message

    def test_update_service_with_row_shaping_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured) as excinfo:
            ChildSpec(
                model=Section,
                fk="catalog",
                update_service=_service,
                field_map={"heading": "title"},
                m2m=lambda row: {"tags": row["tags"]},
            )
        assert "update_service declared alongside field_map, m2m" in str(excinfo.value)

    def test_every_shaping_knob_is_covered(self) -> None:
        for knob in (
            {"field_map": {"heading": "title"}},
            {"exclude_fields": ["title"]},
            {"m2m": lambda row: {}},
            {"children": {"items": ChildSpec(model=Item, fk="section")}},
        ):
            with pytest.raises(ImproperlyConfigured):
                ChildSpec(model=Section, fk="catalog", create_service=_service, **knob)

    def test_reconciliation_knobs_stay_valid_next_to_a_service(self) -> None:
        spec = ChildSpec(
            model=Section,
            fk="catalog",
            match_key="id",
            mode="merge",
            create_service=_service,
            update_service=_service,
            delete_service=_service,
        )
        assert spec.match_key == "id"
        assert spec.mode == "merge"

    def test_delete_service_composes_with_row_shaping(self) -> None:
        # It replaces the unlink-or-delete rule, not the helper call: the
        # cascade still removes grandchildren before handing over the row.
        spec = ChildSpec(
            model=Section,
            fk="catalog",
            delete_service=_service,
            field_map={"heading": "title"},
            children={"items": ChildSpec(model=Item, fk="section")},
        )
        assert spec.delete_service is _service

    def test_shaping_without_a_service_is_untouched(self) -> None:
        spec = ChildSpec(model=Section, fk="catalog", field_map={"heading": "title"})
        assert spec.field_map == {"heading": "title"}
