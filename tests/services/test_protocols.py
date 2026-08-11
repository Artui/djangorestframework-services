"""Tests for the lenient shape of the unified service Protocols.

The Protocols are structural and not runtime-checkable; these tests cover
that ordinary callables matching the shape are accepted as service callables
on a parameterized ``ServiceSpec`` without runtime errors. Static enforcement
is exercised separately via ``ty`` in CI.

The Protocol parameterisation is just input and result types — ``**extras``
is typed as ``Any`` so the framework's kwargs pool (``request``, ``user``,
URL kwargs, ``ServiceSpec.kwargs`` returns) flows in without the service
having to declare them. Strict-typed extras stay on the user's function
signature via ``**extras: Unpack[YourKw]`` (with ``NotRequired`` keys).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from rest_framework_services import (
    CreateService,
    DeleteService,
    NoInput,
    SelectorSpec,
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
    spec: ServiceSpec[_AuthorIn, _Author, Any] = ServiceSpec(service=fn)
    assert spec.service is _create


def test_update_service_protocol_accepts_matching_callable() -> None:
    fn: UpdateService[_AuthorIn, _Author, _Author] = _update
    spec: ServiceSpec[_AuthorIn, _Author, Any] = ServiceSpec(service=fn)
    assert spec.service is _update


def test_delete_service_protocol_accepts_matching_callable() -> None:
    fn: DeleteService[NoInput, _Author, None] = _delete
    spec: ServiceSpec[None, None, Any] = ServiceSpec(service=fn)
    assert spec.service is _delete


def test_service_spec_unparameterized_still_works() -> None:
    """Back-compat: ``ServiceSpec(service=fn)`` without generics is unchanged."""
    spec = ServiceSpec(service=_create)
    assert spec.service is _create
    assert spec.atomic is True


def test_spec_subscripts_are_all_or_nothing() -> None:
    """Parameterising a spec means supplying *every* type argument.

    The annotations above are strings under ``from __future__ import
    annotations``, so they never reach the subscript machinery — which is how
    a partial ``ServiceSpec[InputT, ResultT]`` survived in the docs unnoticed.
    Evaluate the subscripts here so the arity is actually pinned: these are
    plain ``TypeVar``s with no PEP 696 defaults, so dropping ``ExtraT`` is a
    ``TypeError``, not an implicit ``Any``.
    """
    assert ServiceSpec[_AuthorIn, _Author, Any] is not None
    assert SelectorSpec[_Author, Any] is not None

    # ``arguments|parameters`` because the noun is upstream's, not ours:
    # typing_extensions said "Too few parameters" until 4.11.0 and "Too few
    # arguments" from 4.11.0 on. Matching only the newer wording pinned the
    # suite to >=4.11 while the package's declared floor is 4.6 -- the package
    # itself is fine there, so this assertion, not the code, was the thing that
    # made the floor unrunnable. What the test is about is the arity being
    # rejected at all; the wording is incidental.
    too_few = "Too few (arguments|parameters)"
    with pytest.raises(TypeError, match=too_few):
        ServiceSpec[_AuthorIn, _Author]  # type: ignore[misc]
    with pytest.raises(TypeError, match=too_few):
        SelectorSpec[_Author]  # type: ignore[misc]
