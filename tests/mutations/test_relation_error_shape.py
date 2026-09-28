"""A collection's row error has the shape DRF gives a nested serializer's.

A consumer validating ``sections = SectionIn(many=True)`` and writing the rows
through ``children=`` receives both refusals in one API, so the two have to
agree on how a failing row is addressed. DRF has answered that two ways: a list
as long as the incoming one, through 3.17; a mapping of the failing rows'
indexes from 3.18.0, with ``LIST_SERIALIZER_ERRORS_AS_DICT`` (3.18.1, default
``True``) as the switch back until 3.20 removes the list.

The parity test asks the installed DRF rather than restating it, so it holds on
the locked release and on the floor job's alike. The rule's own tests pin the
inputs, because CI only ever installs two releases and the rule has three.
"""

from __future__ import annotations

import warnings
from types import SimpleNamespace
from typing import Any

import pytest
from django.conf import settings
from django.test import override_settings
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from rest_framework_services import ChildSpec, ServiceValidationError, create_from_input
from rest_framework_services.mutations import utils
from rest_framework_services.mutations.utils import _RowPath
from tests.testapp.models import Catalog, Section

_RUDE: dict[str, Any] = {"title": ["Too rude."]}
_ROWS: list[dict[str, Any]] = [{"title": "ok"}, {"title": "rude"}, {"title": "fine"}]


class _SectionIn(serializers.Serializer):
    title = serializers.CharField()

    def validate_title(self, value: str) -> str:
        if value == "rude":
            raise ValidationError("Too rude.")
        return value


class _CatalogIn(serializers.Serializer):
    sections = _SectionIn(many=True)


def _what_a_nested_serializer_says() -> Any:
    serializer = _CatalogIn(data={"sections": _ROWS})
    with warnings.catch_warnings():
        # The list form warns that it goes in 3.20. Which form DRF chose is what
        # is being read here, so the warning is not this test's business.
        warnings.filterwarnings("ignore", message="The list-based error format")
        assert not serializer.is_valid()
    return serializer.errors["sections"]


def _refuses_the_rude_row(*, data: dict[str, Any]) -> Section:
    if data["title"] == "rude":
        raise ServiceValidationError(_RUDE)
    return Section.objects.create(**data)


@pytest.mark.django_db
class TestTheShapeIsDrfsOwn:
    @pytest.mark.parametrize(
        "configured",
        [{}, {"LIST_SERIALIZER_ERRORS_AS_DICT": True}, {"LIST_SERIALIZER_ERRORS_AS_DICT": False}],
        ids=["drf-default", "as-dict", "as-list"],
    )
    def test_a_row_error_matches_a_nested_serializers(self, configured: dict[str, Any]) -> None:
        # On a DRF without the setting both sides ignore it, so every case still
        # compares like with like.
        with override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, **configured}):
            nested = _what_a_nested_serializer_says()
            with pytest.raises(ServiceValidationError) as excinfo:
                create_from_input(
                    Catalog,
                    {"name": "c", "sections": _ROWS},
                    children={
                        "sections": ChildSpec(
                            model=Section, fk="catalog", create_service=_refuses_the_rude_row
                        )
                    },
                )
        assert excinfo.value.detail == {"sections": nested}


class TestWhichShapeTheRuleChooses:
    """The three inputs, pinned, so each arc runs whatever DRF CI installed."""

    def _namespaced(self, monkeypatch: pytest.MonkeyPatch, **drf: Any) -> Any:
        release: tuple[int, ...] = drf.pop("release")
        monkeypatch.setattr(utils, "api_settings", SimpleNamespace(**drf))
        monkeypatch.setattr(utils, "_DRF_RELEASE", release)
        return _RowPath("sections", 1, 3).namespace(_RUDE)

    def test_the_setting_decides_where_drf_has_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._namespaced(
            monkeypatch, release=(3, 18), LIST_SERIALIZER_ERRORS_AS_DICT=False
        ) == {"sections": [{}, _RUDE, {}]}
        assert self._namespaced(
            monkeypatch, release=(3, 18), LIST_SERIALIZER_ERRORS_AS_DICT=True
        ) == {"sections": {1: _RUDE}}

    def test_without_it_a_release_from_3_18_keys_by_index(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 3.18.0: the mapping shipped a patch release before the setting did.
        assert self._namespaced(monkeypatch, release=(3, 18)) == {"sections": {1: _RUDE}}

    def test_without_it_an_older_release_aligns_a_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self._namespaced(monkeypatch, release=(3, 17)) == {"sections": [{}, _RUDE, {}]}

    def test_a_single_row_has_no_position_either_way(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(utils, "api_settings", SimpleNamespace())
        assert _RowPath("profile").namespace(_RUDE) == {"profile": _RUDE}
