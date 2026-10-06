"""Tests for ``provider_keys``: what a ``kwargs=`` provider says it fills, before it runs.

Both spec transports carried a copy of this reader, and these are their tests,
moved here from the schema each built: the MCP server's selector-tool
requiredness tests and the Pydantic-AI ``SpecToolset``'s
``test_a_providers_return_annotation_decides_what_the_model_is_asked_for``. Each
asserts on the reader's answer, which is what both schemas were built from.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar, Union

import pytest
from typing_extensions import NotRequired, TypedDict

from rest_framework_services.dispatch.provider_keys import provider_keys
from rest_framework_services.types.unset import UNSET, UnsetType

_T = TypeVar("_T")

_NOTHING: frozenset[str] = frozenset()


class _ScopeBase(TypedDict):
    tenant: str


class _Scope(_ScopeBase, total=False):
    # ``total=False`` rather than ``NotRequired[...]``: under this module's
    # postponed annotations the qualifier is a string ``TypedDict`` does not
    # read, and ``region`` would land in ``__required_keys__``.
    region: str


class _GenericScope(TypedDict, Generic[_T]):
    tenant: _T


def _typed_provider() -> _Scope:
    return {"tenant": "acme"}


def _generic_provider() -> _GenericScope[str]:
    return {"tenant": "acme"}


def _plain_dict_provider() -> dict[str, Any]:
    return {"tenant": "acme"}


def _unresolvable_provider() -> Any:
    return {"tenant": "acme"}


# A return annotation naming a type that does not exist. Assigned rather than
# written in the signature, where a linter would refuse the undefined name.
_unresolvable_provider.__annotations__ = {"return": "NoSuchScope"}


def test_a_spec_without_a_provider_fills_nothing() -> None:
    # Nothing, which is not the "cannot tell" of an untyped provider: a
    # transport requires every parameter without a default beside this one,
    # and none beside that one.
    assert provider_keys(None) == (_NOTHING, _NOTHING)


def test_every_key_of_a_typed_dict_is_filled_optional_ones_included() -> None:
    # ``region`` is optional in the ``TypedDict``, but the provider owns it, so
    # it is filled as much as ``tenant`` is.
    assert provider_keys(_typed_provider) == (frozenset({"tenant", "region"}), _NOTHING)


def test_a_parameterised_typed_dict_is_read_off_its_origin() -> None:
    # The alias does not relay ``__required_keys__``; its origin does.
    assert provider_keys(_generic_provider) == (frozenset({"tenant"}), _NOTHING)


@pytest.mark.parametrize(
    "provider",
    [_plain_dict_provider, lambda view: {"tenant": "acme"}, _unresolvable_provider],
    ids=["plain-dict", "unannotated", "unresolvable-return"],
)
def test_a_provider_whose_return_says_nothing_is_untyped(provider: Any) -> None:
    # ``None``: it may fill any name, so a transport requires none of them.
    assert provider_keys(provider) is None


# ``UnsetType`` in a value: drf-services lets a provider decline a key with
# ``UNSET``, which ``resolve_provider`` removes from the pool, so a declined key
# does not satisfy the parameter and is returned apart from the filled ones.
class _MaybeScope(TypedDict):
    tenant: str | UnsetType
    region: str


class _MaybeUnionScope(TypedDict):
    tenant: Union[str, UnsetType]  # noqa: UP007 -- the spelling under test
    region: str


class _MaybeNotRequiredScope(TypedDict):
    tenant: NotRequired[str | UnsetType]
    region: str


# The same wrapper through the functional syntax, which the postponed
# annotations above do not hide from the ``TypedDict`` machinery.
_FunctionalMaybeScope = TypedDict(  # noqa: UP013
    "_FunctionalMaybeScope",
    {"tenant": NotRequired[str | UnsetType], "region": str},
)


def _maybe_provider() -> _MaybeScope:
    return {"tenant": UNSET, "region": "eu"}


def _maybe_union_provider() -> _MaybeUnionScope:
    return {"tenant": UNSET, "region": "eu"}


def _maybe_not_required_provider() -> _MaybeNotRequiredScope:
    return {"region": "eu"}


def _functional_maybe_provider() -> _FunctionalMaybeScope:
    return {"region": "eu"}


@pytest.mark.parametrize(
    "provider",
    [
        _maybe_provider,
        _maybe_union_provider,
        _maybe_not_required_provider,
        _functional_maybe_provider,
    ],
    ids=["pep-604-union", "typing-union", "not-required", "functional-not-required"],
)
def test_a_key_whose_value_admits_unset_may_be_declined(provider: Any) -> None:
    assert provider_keys(provider) == (frozenset({"region"}), frozenset({"tenant"}))


class _NoneableScope(TypedDict):
    tenant: str | None
    region: str


def _noneable_provider() -> _NoneableScope:
    return {"tenant": None, "region": "eu"}


def test_a_union_that_does_not_admit_unset_is_still_filled() -> None:
    # Having arguments is not enough: ``str | None`` is a union, and ``None`` is
    # a value the provider fills the key with, not a refusal to.
    assert provider_keys(_noneable_provider) == (frozenset({"tenant", "region"}), _NOTHING)
