"""Tests for the lenient list / retrieve / output selector Protocols."""

from __future__ import annotations

from dataclasses import dataclass

from rest_framework.request import Request

from rest_framework_services import (
    ListSelector,
    OutputSelector,
    RetrieveSelector,
    SelectorSpec,
)
from rest_framework_services.services.utils import UserT


@dataclass
class _Author:
    id: int
    name: str


def _list(*, request: Request, user: UserT) -> list[_Author]:
    return [_Author(id=1, name="A")]


def _retrieve(*, request: Request, user: UserT, pk: int) -> _Author | None:
    return _Author(id=pk, name="A")


def _output(*, result: _Author, request: Request, user: UserT) -> _Author:
    return result


def test_list_selector_protocol_accepts_matching_callable() -> None:
    fn: ListSelector[_Author] = _list
    spec: SelectorSpec[list[_Author]] = SelectorSpec(selector=fn)
    assert spec.selector is _list


def test_retrieve_selector_protocol_accepts_matching_callable() -> None:
    fn: RetrieveSelector[_Author] = _retrieve
    spec: SelectorSpec[_Author | None] = SelectorSpec(selector=fn)
    assert spec.selector is _retrieve


def test_output_selector_protocol_accepts_matching_callable() -> None:
    fn: OutputSelector[_Author, _Author] = _output
    assert fn is _output


def test_selector_spec_unparameterized_still_works() -> None:
    spec = SelectorSpec(selector=_list)
    assert spec.selector is _list
    assert spec.output_serializer is None
