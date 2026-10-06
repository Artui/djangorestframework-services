"""``strip_hidden_inputs`` — the caller-mapping half of ``NotClientInput``."""

from __future__ import annotations

from typing import Annotated, Any

from django.http import QueryDict
from typing_extensions import TypedDict, Unpack

from rest_framework_services import NotClientInput
from rest_framework_services.dispatch.strip_hidden_inputs import strip_hidden_inputs


def _unmarked(*, team: str = "own-team") -> str:
    return team


def _marked(*, team: Annotated[str, NotClientInput] = "own-team", limit: int = 10) -> str:
    return team


class _Extras(TypedDict, total=False):
    team: Annotated[str, NotClientInput]
    limit: int


def _marked_in_extras(**extras: Unpack[_Extras]) -> Any:
    return extras


def test_an_unmarked_callable_gets_the_mapping_back_unchanged() -> None:
    """Identity, not a copy: a form-encoded body stays a ``QueryDict``, whose
    values a dict copy would turn into one-element lists."""
    params = QueryDict("team=other-team&limit=3")

    assert strip_hidden_inputs(params, _unmarked) is params


def test_only_the_marked_key_is_dropped() -> None:
    assert strip_hidden_inputs({"team": "other-team", "limit": 3}, _marked) == {"limit": 3}


def test_a_key_marked_inside_an_unpacked_typed_dict_is_dropped() -> None:
    assert strip_hidden_inputs({"team": "other-team", "limit": 3}, _marked_in_extras) == {
        "limit": 3
    }
