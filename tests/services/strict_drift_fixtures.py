"""Fixtures that intentionally drift from the unified service / selector
Protocols (used in their strict, fully-parameterised shape). Run by
``make type-check-strict-fixtures`` — ``ty`` is expected to emit one
diagnostic per ``# expect-error`` marker. Used to guard against regressions
where the strict parameterisation silently stops validating drift.

Note on coverage in ``ty``: this file is intentionally outside the package's
``ty`` scope (CI runs ``ty check rest_framework_services`` for the green
build). The ``type-check-strict-fixtures`` target invokes ``ty`` on this file
specifically and asserts an error count.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Unpack

from typing_extensions import TypedDict

from rest_framework_services import (
    CreateService,
    DeleteService,
    ListSelector,
    NoInput,
    OutputSelector,
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


class _OutputExtras(TypedDict):
    rendered_at: str


# ---------------------------------------------------------------------------
# Drift case 1: create service — wrong return type
# ---------------------------------------------------------------------------


# expect-error: incompatible return types
@implements(CreateService[_AuthorIn, _Author, _CreateExtras])
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
@implements(CreateService[_AuthorIn, _Author, _CreateExtras])
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
@implements(UpdateService[_AuthorIn, _Author, _Author, _UpdateExtras])
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
@implements(DeleteService[NoInput, _Author, None, _DeleteExtras])
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
@implements(ListSelector[_Author, _ListExtras])
def list_drift_element(
    **extras: Unpack[_ListExtras],
) -> list[int]:
    return []


# ---------------------------------------------------------------------------
# Drift case 6: retrieve selector — wrong return type
# ---------------------------------------------------------------------------


# expect-error: incompatible return types
@implements(RetrieveSelector[_Author, _RetrieveExtras])
def retrieve_drift_return(
    **extras: Unpack[_RetrieveExtras],
) -> int | None:
    return None


# ---------------------------------------------------------------------------
# Drift case 7: output selector — wrong input type
# ---------------------------------------------------------------------------


# expect-error: incompatible parameter type
@implements(OutputSelector[_Author, _Author, _OutputExtras])
def output_drift_input(
    *,
    result: int,
    **extras: Unpack[_OutputExtras],
) -> _Author:
    return _Author(id=result, name="x")
