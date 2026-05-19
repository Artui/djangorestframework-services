"""Tests for the ``implements`` identity decorator.

Runtime behaviour only — static drift detection is exercised via ``ty`` in
CI against ``tests/services/strict_drift_fixtures.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Unpack

from typing_extensions import TypedDict

from rest_framework_services import (
    CreateService,
    implements,
)


@dataclass
class _AuthorIn:
    name: str


@dataclass
class _Author:
    id: int
    name: str


class _CreateExtras(TypedDict):
    tenant_id: int


def test_implements_decorator_returns_function_unchanged() -> None:
    def create_author(
        *,
        data: _AuthorIn,
        **extras: Unpack[_CreateExtras],
    ) -> _Author:
        return _Author(id=extras["tenant_id"], name=data.name)

    decorated = implements(CreateService[_AuthorIn, _Author, _CreateExtras])(create_author)

    assert decorated is create_author
    assert create_author.__name__ == "create_author"


def test_implements_direct_call_is_identity() -> None:
    def fn(
        *,
        data: _AuthorIn,
        **extras: Unpack[_CreateExtras],
    ) -> _Author:
        return _Author(id=extras["tenant_id"], name=data.name)

    decorated = implements(CreateService[_AuthorIn, _Author, _CreateExtras])(fn)
    assert decorated is fn
