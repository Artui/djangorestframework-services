"""Tests for strict-typed extras on user service / selector functions.

Under the unified Protocols, strict-extras typing lives on the *user's*
function signature (``**extras: Unpack[YourKw]``) rather than as a third
Protocol type argument. The Protocol itself uses ``**extras: Any`` so the
function-level annotation provides editor / type-checker assistance on
``extras["..."]`` accesses without breaking Protocol conformance.

Strict ``TypedDict`` keys must be ``NotRequired`` — the framework's kwargs
pool changes shape per view/action, and a Protocol-conformant function must
remain callable without those keys.

The Protocols are structural; full static enforcement is exercised separately
via ``ty`` in CI against ``tests/services/strict_drift_fixtures.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import NotRequired, TypedDict, Unpack

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
    tenant_id: NotRequired[int]


class _UpdateExtras(TypedDict):
    tenant_id: NotRequired[int]
    actor_id: NotRequired[int]


class _DeleteExtras(TypedDict):
    reason: NotRequired[str]


class _ListExtras(TypedDict):
    tenant_id: NotRequired[int]


class _RetrieveExtras(TypedDict):
    pk: NotRequired[int]
    tenant_id: NotRequired[int]


class _OutputExtras(TypedDict):
    rendered_at: NotRequired[str]


class _CreateHttpExtras(HttpExtras[_User]):
    tenant_id: NotRequired[int]


class _UpdateHttpExtras(HttpExtras[_User]):
    tenant_id: NotRequired[int]
    actor_id: NotRequired[int]


class _DeleteHttpExtras(HttpExtras[_User]):
    reason: NotRequired[str]


class _ListHttpExtras(HttpExtras[_User]):
    tenant_id: NotRequired[int]


class _RetrieveHttpExtras(HttpExtras[_User]):
    pk: NotRequired[int]
    tenant_id: NotRequired[int]


class _OutputHttpExtras(HttpExtras[_User]):
    rendered_at: NotRequired[str]


def _create(
    *,
    data: _AuthorIn,
    **extras: Unpack[_CreateExtras],
) -> _Author:
    return _Author(id=extras.get("tenant_id", 0), name=data.name)


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
    return _Author(id=extras.get("pk", 0), name="A")


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
    user = extras.get("user")
    user_name = user.name if user is not None else "anon"
    return _Author(id=extras.get("tenant_id", 0), name=f"{user_name}:{data.name}")


def _update_http(
    *,
    instance: _Author,
    data: _AuthorIn,
    **extras: Unpack[_UpdateHttpExtras],
) -> _Author:
    user = extras.get("user")
    user_name = user.name if user is not None else "anon"
    instance.name = f"{user_name}:{data.name}"
    return instance


def _delete_http(
    *,
    instance: _Author,
    **extras: Unpack[_DeleteHttpExtras],
) -> None:
    return None


def _list_http(
    **extras: Unpack[_ListHttpExtras],
) -> list[_Author]:
    return []


def _retrieve_http(
    **extras: Unpack[_RetrieveHttpExtras],
) -> _Author | None:
    return _Author(id=extras.get("pk", 0), name="A")


def _output_http(
    *,
    result: _Author,
    **extras: Unpack[_OutputHttpExtras],
) -> _Author:
    return result


def test_create_service_accepts_strict_extras_callable() -> None:
    fn: CreateService[_AuthorIn, _Author] = _create
    assert fn is _create


def test_update_service_accepts_strict_extras_callable() -> None:
    fn: UpdateService[_AuthorIn, _Author, _Author] = _update
    assert fn is _update


def test_delete_service_accepts_strict_extras_callable() -> None:
    fn: DeleteService[NoInput, _Author, None] = _delete
    assert fn is _delete


def test_list_selector_accepts_strict_extras_callable() -> None:
    fn: ListSelector[_Author] = _list
    assert fn is _list


def test_retrieve_selector_accepts_strict_extras_callable() -> None:
    fn: RetrieveSelector[_Author] = _retrieve
    assert fn is _retrieve


def test_output_selector_accepts_strict_extras_callable() -> None:
    fn: OutputSelector[_Author, _Author] = _output
    assert fn is _output


def test_create_service_accepts_http_extras_callable() -> None:
    fn: CreateService[_AuthorIn, _Author] = _create_http
    assert fn is _create_http


def test_update_service_accepts_http_extras_callable() -> None:
    fn: UpdateService[_AuthorIn, _Author, _Author] = _update_http
    assert fn is _update_http


def test_delete_service_accepts_http_extras_callable() -> None:
    fn: DeleteService[NoInput, _Author, None] = _delete_http
    assert fn is _delete_http


def test_list_selector_accepts_http_extras_callable() -> None:
    fn: ListSelector[_Author] = _list_http
    assert fn is _list_http


def test_retrieve_selector_accepts_http_extras_callable() -> None:
    fn: RetrieveSelector[_Author] = _retrieve_http
    assert fn is _retrieve_http


def test_output_selector_accepts_http_extras_callable() -> None:
    fn: OutputSelector[_Author, _Author] = _output_http
    assert fn is _output_http
