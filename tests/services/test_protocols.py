"""Tests for the lenient shape of the unified service Protocols.

The Protocols are structural and not runtime-checkable; these tests cover
that ordinary callables matching the shape are accepted as service callables
on a parameterized ``ServiceSpec`` without runtime errors. Static enforcement
is exercised separately via ``ty`` in CI.

The lenient shape is the unified Protocol parameterised with just input and
result types — ``ExtraT`` defaults to a private arbitrary-key ``TypedDict``
that accepts the framework's kwargs pool (``request``, ``user``, URL kwargs,
``ServiceSpec.kwargs`` returns) without the service declaring them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_services import (
    CreateService,
    DeleteService,
    NoInput,
    ServiceSpec,
    UpdateService,
)


@dataclass
class _AuthorIn:
    name: str


@dataclass
class _Author:
    id: int
    name: str


def _create(*, data: _AuthorIn, **kwargs: Any) -> _Author:
    return _Author(id=1, name=data.name)


def _update(*, instance: _Author, data: _AuthorIn, **kwargs: Any) -> _Author:
    instance.name = data.name
    return instance


def _delete(*, instance: _Author, **kwargs: Any) -> None:
    return None


def test_create_service_protocol_accepts_matching_callable() -> None:
    fn: CreateService[_AuthorIn, _Author] = _create
    spec: ServiceSpec[_AuthorIn, _Author] = ServiceSpec(service=fn)
    assert spec.service is _create


def test_update_service_protocol_accepts_matching_callable() -> None:
    fn: UpdateService[_AuthorIn, _Author, _Author] = _update
    spec: ServiceSpec[_AuthorIn, _Author] = ServiceSpec(service=fn)
    assert spec.service is _update


def test_delete_service_protocol_accepts_matching_callable() -> None:
    fn: DeleteService[NoInput, _Author, None] = _delete
    spec: ServiceSpec[None, None] = ServiceSpec(service=fn)
    assert spec.service is _delete


def test_service_spec_unparameterized_still_works() -> None:
    """Back-compat: ``ServiceSpec(service=fn)`` without generics is unchanged."""
    spec = ServiceSpec(service=_create)
    assert spec.service is _create
    assert spec.atomic is True
