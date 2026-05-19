"""Tests for the strict shape of the unified service / selector Protocols.

After the 0.11.0 merge there is no separate ``StrictCreateService`` class —
the strict shape is the unified Protocol parameterised with an explicit
``ExtraT`` ``TypedDict`` instead of letting it default to the private
arbitrary-key sentinel.

The Protocols are structural; full static enforcement is exercised separately
via ``ty`` in CI against ``tests/services/strict_drift_fixtures.py``.

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
from typing import Unpack

from typing_extensions import TypedDict

from rest_framework_services import (
    CreateService,
    DeleteService,
    HttpExtras,
    ListSelector,
    NoInput,
    OutputSelector,
    RetrieveSelector,
    UpdateService,
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


def test_create_service_strict_accepts_matching_callable() -> None:
    fn: CreateService[_AuthorIn, _Author, _CreateExtras] = _create
    assert fn is _create


def test_update_service_strict_accepts_matching_callable() -> None:
    fn: UpdateService[_AuthorIn, _Author, _Author, _UpdateExtras] = _update
    assert fn is _update


def test_delete_service_strict_accepts_matching_callable() -> None:
    fn: DeleteService[NoInput, _Author, None, _DeleteExtras] = _delete
    assert fn is _delete


def test_list_selector_strict_accepts_matching_callable() -> None:
    fn: ListSelector[_Author, _ListExtras] = _list
    assert fn is _list


def test_retrieve_selector_strict_accepts_matching_callable() -> None:
    fn: RetrieveSelector[_Author, _RetrieveExtras] = _retrieve
    assert fn is _retrieve


def test_output_selector_strict_accepts_matching_callable() -> None:
    fn: OutputSelector[_Author, _Author, _OutputExtras] = _output
    assert fn is _output


def test_create_service_strict_accepts_http_extras_callable() -> None:
    fn: CreateService[_AuthorIn, _Author, _CreateHttpExtras] = _create_http
    assert fn is _create_http


def test_update_service_strict_accepts_http_extras_callable() -> None:
    fn: UpdateService[_AuthorIn, _Author, _Author, _UpdateHttpExtras] = _update_http
    assert fn is _update_http


def test_delete_service_strict_accepts_http_extras_callable() -> None:
    fn: DeleteService[NoInput, _Author, None, _DeleteHttpExtras] = _delete_http
    assert fn is _delete_http


def test_list_selector_strict_accepts_http_extras_callable() -> None:
    fn: ListSelector[_Author, _ListHttpExtras] = _list_http
    assert fn is _list_http


def test_retrieve_selector_strict_accepts_http_extras_callable() -> None:
    fn: RetrieveSelector[_Author, _RetrieveHttpExtras] = _retrieve_http
    assert fn is _retrieve_http


def test_output_selector_strict_accepts_http_extras_callable() -> None:
    fn: OutputSelector[_Author, _Author, _OutputHttpExtras] = _output_http
    assert fn is _output_http
