"""Fixtures that intentionally drift from the unified service / selector
Protocols. Run by ``make type-check-strict-fixtures`` — ``ty`` is expected to
emit one diagnostic per ``# expect-error`` marker. Used to guard against
regressions where the Protocols silently stop validating drift.

Each function is annotated with ``**extras: Unpack[<MyKw>]`` (with
``NotRequired`` keys, so it stays Protocol-conformant) and then assigned to
the matching Protocol type. ty flags the structural mismatch on the
``@implements`` line.

Note on coverage in ``ty``: this file is intentionally outside the package's
``ty`` scope (CI runs ``ty check rest_framework_services`` for the green
build). The ``type-check-strict-fixtures`` target invokes ``ty`` on this file
specifically and asserts an error count.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import NotRequired, TypedDict, Unpack

from rest_framework_services import (
    CreateService,
    DeleteService,
    ListSelector,
    NoInput,
    RetrieveSelector,
    UpdateService,
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


# ---------------------------------------------------------------------------
# Drift case 1: create service — wrong return type
# ---------------------------------------------------------------------------


# expect-error: incompatible return types
@implements(CreateService[_AuthorIn, _Author])
def create_drift_return(
    *,
    data: _AuthorIn,
    **extras: Unpack[_CreateExtras],
) -> _AuthorIn:
    return data


# ---------------------------------------------------------------------------
# Drift case 2: create service — wrong input type
# ---------------------------------------------------------------------------


# expect-error: incompatible parameter type
@implements(CreateService[_AuthorIn, _Author])
def create_drift_input(
    *,
    data: int,
    **extras: Unpack[_CreateExtras],
) -> _Author:
    return _Author(id=data, name="x")


# ---------------------------------------------------------------------------
# Drift case 3: update service — wrong instance type
# ---------------------------------------------------------------------------


# expect-error: incompatible instance type
@implements(UpdateService[_AuthorIn, _Author, _Author])
def update_drift_instance(
    *,
    instance: int,
    data: _AuthorIn,
    **extras: Unpack[_UpdateExtras],
) -> _Author:
    return _Author(id=instance, name=data.name)


# ---------------------------------------------------------------------------
# Drift case 4: delete service — wrong return type
# ---------------------------------------------------------------------------


# expect-error: incompatible return types
@implements(DeleteService[NoInput, _Author, None])
def delete_drift_return(
    *,
    instance: _Author,
    **extras: Unpack[_DeleteExtras],
) -> _Author:
    return instance


# ---------------------------------------------------------------------------
# Drift case 5: list selector — wrong yielded type
# ---------------------------------------------------------------------------


# expect-error: incompatible iterable element type
@implements(ListSelector[_Author])
def list_drift_element(
    **extras: Unpack[_ListExtras],
) -> list[int]:
    return []


# ---------------------------------------------------------------------------
# Drift case 6: retrieve selector — wrong return type
# ---------------------------------------------------------------------------


# expect-error: incompatible return types
@implements(RetrieveSelector[_Author])
def retrieve_drift_return(
    **extras: Unpack[_RetrieveExtras],
) -> int | None:
    return None
