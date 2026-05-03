"""Tests for the strict service / selector Protocols.

The strict Protocols use ``**extras: Unpack[ExtraT]`` to pin the extra-kwargs
contract delivered by ``ServiceSpec.kwargs`` / ``SelectorSpec.kwargs``. These
tests cover that ordinary callables matching the shape are accepted; full
static enforcement (drift detection) is exercised separately via ``ty`` in
CI against ``tests/services/strict_drift_fixtures.py``.

Two flavours of fixture:

* ``_create`` / ``_update`` / ... — minimal: declare only what the strict
  Protocol requires (``data`` / ``instance`` / ``result``) plus an
  ``ExtraT`` ``TypedDict`` with no HTTP keys. Proves a strict service does
  not need ``request`` / ``user`` to satisfy the Protocol.
* ``_create_http`` / ``_update_http`` / ... — HTTP-bound: ``ExtraT`` extends
  :class:`HttpExtras` so the service can read ``extras['request']`` /
  ``extras['user']``. Proves the canonical way to opt back in.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import TypedDict, Unpack

from rest_framework_services import (
    HttpExtras,
    NoInput,
    StrictCreateService,
    StrictDeleteService,
    StrictListSelector,
    StrictOutputSelector,
    StrictRetrieveSelector,
    StrictUpdateService,
)


@dataclass
class _AuthorIn:
    name: str


@dataclass
class _Author:
    id: int
    name: str


class _User:
    def __init__(self, name: str) -> None:
        self.name = name


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


class _CreateHttpExtras(HttpExtras[_User]):
    tenant_id: int


class _UpdateHttpExtras(HttpExtras[_User]):
    tenant_id: int
    actor_id: int


class _DeleteHttpExtras(HttpExtras[_User]):
    reason: str


class _ListHttpExtras(HttpExtras[_User]):
    tenant_id: int


class _RetrieveHttpExtras(HttpExtras[_User]):
    pk: int
    tenant_id: int


class _OutputHttpExtras(HttpExtras[_User]):
    rendered_at: str


def _create(
    *,
    data: _AuthorIn,
    **extras: Unpack[_CreateExtras],
) -> _Author:
    return _Author(id=extras["tenant_id"], name=data.name)


def _update(
    *,
    instance: _Author,
    data: _AuthorIn,
    **extras: Unpack[_UpdateExtras],
) -> _Author:
    instance.name = data.name
    return instance


def _delete(
    *,
    instance: _Author,
    **extras: Unpack[_DeleteExtras],
) -> None:
    return None


def _list(
    **extras: Unpack[_ListExtras],
) -> list[_Author]:
    return []


def _retrieve(
    **extras: Unpack[_RetrieveExtras],
) -> _Author | None:
    return _Author(id=extras["pk"], name="A")


def _output(
    *,
    result: _Author,
    **extras: Unpack[_OutputExtras],
) -> _Author:
    return result


def _create_http(
    *,
    data: _AuthorIn,
    **extras: Unpack[_CreateHttpExtras],
) -> _Author:
    return _Author(id=extras["tenant_id"], name=f"{extras['user'].name}:{data.name}")


def _update_http(
    *,
    instance: _Author,
    data: _AuthorIn,
    **extras: Unpack[_UpdateHttpExtras],
) -> _Author:
    instance.name = f"{extras['user'].name}:{data.name}"
    return instance


def _delete_http(
    *,
    instance: _Author,
    **extras: Unpack[_DeleteHttpExtras],
) -> None:
    _ = extras["request"], extras["user"]
    return None


def _list_http(
    **extras: Unpack[_ListHttpExtras],
) -> list[_Author]:
    _ = extras["request"], extras["user"]
    return []


def _retrieve_http(
    **extras: Unpack[_RetrieveHttpExtras],
) -> _Author | None:
    _ = extras["request"], extras["user"]
    return _Author(id=extras["pk"], name="A")


def _output_http(
    *,
    result: _Author,
    **extras: Unpack[_OutputHttpExtras],
) -> _Author:
    _ = extras["request"], extras["user"]
    return result


def test_strict_create_service_accepts_matching_callable() -> None:
    fn: StrictCreateService[_AuthorIn, _CreateExtras, _Author] = _create
    assert fn is _create


def test_strict_update_service_accepts_matching_callable() -> None:
    fn: StrictUpdateService[_AuthorIn, _Author, _UpdateExtras, _Author] = _update
    assert fn is _update


def test_strict_delete_service_accepts_matching_callable() -> None:
    fn: StrictDeleteService[NoInput, _Author, _DeleteExtras, None] = _delete
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


def test_strict_create_service_accepts_http_extras_callable() -> None:
    fn: StrictCreateService[_AuthorIn, _CreateHttpExtras, _Author] = _create_http
    assert fn is _create_http


def test_strict_update_service_accepts_http_extras_callable() -> None:
    fn: StrictUpdateService[_AuthorIn, _Author, _UpdateHttpExtras, _Author] = _update_http
    assert fn is _update_http


def test_strict_delete_service_accepts_http_extras_callable() -> None:
    fn: StrictDeleteService[NoInput, _Author, _DeleteHttpExtras, None] = _delete_http
    assert fn is _delete_http


def test_strict_list_selector_accepts_http_extras_callable() -> None:
    fn: StrictListSelector[_ListHttpExtras, _Author] = _list_http
    assert fn is _list_http


def test_strict_retrieve_selector_accepts_http_extras_callable() -> None:
    fn: StrictRetrieveSelector[_RetrieveHttpExtras, _Author] = _retrieve_http
    assert fn is _retrieve_http


def test_strict_output_selector_accepts_http_extras_callable() -> None:
    fn: StrictOutputSelector[_Author, _OutputHttpExtras, _Author] = _output_http
    assert fn is _output_http
