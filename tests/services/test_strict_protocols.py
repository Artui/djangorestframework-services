"""Tests for the strict service / selector Protocols.

The strict Protocols use ``**extras: Unpack[ExtraT]`` to pin the extra-kwargs
contract delivered by ``ServiceSpec.kwargs`` / ``SelectorSpec.kwargs``. These
tests cover that ordinary callables matching the shape are accepted; full
static enforcement (drift detection) is exercised separately via ``ty`` in
CI against ``tests/services/strict_drift_fixtures.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from rest_framework.request import Request
from typing_extensions import TypedDict, Unpack

from rest_framework_services import (
    StrictCreateService,
    StrictDeleteService,
    StrictListSelector,
    StrictOutputSelector,
    StrictRetrieveSelector,
    StrictUpdateService,
)
from rest_framework_services.services.utils import UserT


@dataclass
class _AuthorIn:
    name: str


@dataclass
class _Author:
    id: int
    name: str


class _CreateExtras(TypedDict):
    tenant_id: int


class _UpdateExtras(TypedDict):
    tenant_id: int
    actor_id: int


class _DeleteExtras(TypedDict):
    reason: str


class _ListExtras(TypedDict):
    tenant_id: int


class _RetrieveExtras(TypedDict):
    pk: int
    tenant_id: int


class _OutputExtras(TypedDict):
    rendered_at: str


def _create(
    *,
    data: _AuthorIn,
    request: Request,
    user: UserT,
    **extras: Unpack[_CreateExtras],
) -> _Author:
    return _Author(id=extras["tenant_id"], name=data.name)


def _update(
    *,
    instance: _Author,
    data: _AuthorIn,
    request: Request,
    user: UserT,
    **extras: Unpack[_UpdateExtras],
) -> _Author:
    instance.name = data.name
    return instance


def _delete(
    *,
    instance: _Author,
    request: Request,
    user: UserT,
    **extras: Unpack[_DeleteExtras],
) -> None:
    return None


def _list(
    *,
    request: Request,
    user: UserT,
    **extras: Unpack[_ListExtras],
) -> list[_Author]:
    return []


def _retrieve(
    *,
    request: Request,
    user: UserT,
    **extras: Unpack[_RetrieveExtras],
) -> _Author | None:
    return _Author(id=extras["pk"], name="A")


def _output(
    *,
    result: _Author,
    request: Request,
    user: UserT,
    **extras: Unpack[_OutputExtras],
) -> _Author:
    return result


def test_strict_create_service_accepts_matching_callable() -> None:
    fn: StrictCreateService[_AuthorIn, _CreateExtras, _Author] = _create
    assert fn is _create


def test_strict_update_service_accepts_matching_callable() -> None:
    fn: StrictUpdateService[_AuthorIn, _Author, _UpdateExtras, _Author] = _update
    assert fn is _update


def test_strict_delete_service_accepts_matching_callable() -> None:
    fn: StrictDeleteService[_Author, _DeleteExtras, None] = _delete
    assert fn is _delete


def test_strict_list_selector_accepts_matching_callable() -> None:
    fn: StrictListSelector[_ListExtras, _Author] = _list
    assert fn is _list


def test_strict_retrieve_selector_accepts_matching_callable() -> None:
    fn: StrictRetrieveSelector[_RetrieveExtras, _Author] = _retrieve
    assert fn is _retrieve


def test_strict_output_selector_accepts_matching_callable() -> None:
    fn: StrictOutputSelector[_Author, _OutputExtras, _Author] = _output
    assert fn is _output
