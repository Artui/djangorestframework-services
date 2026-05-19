"""Tests for the generic ``HttpExtras[UserT]`` ``TypedDict``."""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from typing_extensions import Unpack, assert_type

from rest_framework_services import HttpExtras


class _User:
    """Minimal stand-in for a project-local AUTH_USER_MODEL."""

    def __init__(self, name: str) -> None:
        self.name = name


def test_keys_are_not_required() -> None:
    # ``request`` and ``user`` are ``NotRequired`` — the framework's kwargs
    # pool may omit them (or the service may be called outside HTTP scope).
    assert HttpExtras.__required_keys__ == frozenset()
    assert HttpExtras.__optional_keys__ == frozenset({"request", "user"})


def test_runtime_usage_with_default_user_type() -> None:
    request: Request = APIRequestFactory().get("/")
    extras: HttpExtras = {"request": request, "user": None}
    assert extras["request"] is request
    assert extras["user"] is None


def test_runtime_usage_with_concrete_user_type() -> None:
    request: Request = APIRequestFactory().get("/")
    user = _User(name="bob")
    extras: HttpExtras[_User] = {"request": request, "user": user}
    assert extras["user"].name == "bob"


def test_runtime_usage_omitting_keys() -> None:
    # All keys are optional — the empty dict is also a valid HttpExtras.
    extras: HttpExtras = {}
    assert extras == {}


def test_subclass_extends_with_not_required_extra_keys() -> None:
    class CreateAuthorKwargs(HttpExtras[_User], total=False):
        tenant_id: int

    # All keys carry forward as optional; the new key is also optional.
    assert CreateAuthorKwargs.__required_keys__ == frozenset()
    assert CreateAuthorKwargs.__optional_keys__ == frozenset({"request", "user", "tenant_id"})


def test_can_be_unpacked_into_strict_signature() -> None:
    """A service taking ``**extras: Unpack[HttpExtras[...]]`` works at runtime."""

    class CreateAuthorKwargs(HttpExtras[_User], total=False):
        tenant_id: int

    def create_author(**extras: Unpack[CreateAuthorKwargs]) -> str:
        user = extras.get("user")
        name = user.name if user is not None else "anon"
        return f"{name}:{extras.get('tenant_id', 0)}"

    request: Request = APIRequestFactory().get("/")
    result = create_author(request=request, user=_User(name="alice"), tenant_id=7)
    assert result == "alice:7"


def test_user_field_narrows_under_subscription() -> None:
    """Static-typing assertion: ``HttpExtras[T]`` narrows ``user`` to ``T | None``."""
    request: Request = APIRequestFactory().get("/")

    typed: HttpExtras[_User] = {"request": request, "user": _User(name="x")}
    assert_type(typed.get("user"), "_User | None")

    untyped: HttpExtras = {"request": request, "user": object()}
    assert_type(untyped.get("user"), Any)
