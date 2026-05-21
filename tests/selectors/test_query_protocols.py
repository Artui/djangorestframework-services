"""Tests for the lenient shape of the unified list / retrieve selector Protocols."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_services import (
    ListSelector,
    RetrieveSelector,
    SelectorKind,
    SelectorSpec,
)


@dataclass
class _Author:
    id: int
    name: str


def _list(**kwargs: Any) -> list[_Author]:
    return [_Author(id=1, name="A")]


def _retrieve(*, pk: int, **kwargs: Any) -> _Author | None:
    return _Author(id=pk, name="A")


def test_list_selector_protocol_accepts_matching_callable() -> None:
    fn: ListSelector[_Author] = _list
    spec: SelectorSpec[list[_Author]] = SelectorSpec(kind=SelectorKind.LIST, selector=fn)
    assert spec.selector is _list


def test_retrieve_selector_protocol_accepts_matching_callable() -> None:
    fn: RetrieveSelector[_Author] = _retrieve
    spec: SelectorSpec[_Author | None] = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=fn)
    assert spec.selector is _retrieve


def test_selector_spec_unparameterized_still_works() -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_list)
    assert spec.selector is _list
    assert spec.output_serializer is None
