"""Unit tests for ``unpack_typed_dict``."""

from __future__ import annotations

from typing import Any, Generic

from typing_extensions import TypedDict, TypeVar, Unpack

from rest_framework_services.types.unpack_typed_dict import unpack_typed_dict

_UserT = TypeVar("_UserT", default=Any)


class _Plain(TypedDict, total=False):
    a: int


class _Generic(TypedDict, Generic[_UserT], total=False):
    user: _UserT


def test_returns_typed_dict_for_unpack_of_plain() -> None:
    assert unpack_typed_dict(Unpack[_Plain]) is _Plain


def test_resolves_generic_alias_to_origin_class() -> None:
    # ``Unpack[HttpExtras[MyUser]]`` — the arg is a parameterised alias, so the
    # origin class must be recovered before the ``is_typeddict`` check.
    assert unpack_typed_dict(Unpack[_Generic[int]]) is _Generic


def test_none_for_unpack_of_non_typed_dict() -> None:
    assert unpack_typed_dict(Unpack[int]) is None


def test_none_for_non_unpack_annotation() -> None:
    assert unpack_typed_dict(int) is None
    assert unpack_typed_dict(dict[str, int]) is None


def test_none_for_missing_annotation() -> None:
    assert unpack_typed_dict(None) is None
