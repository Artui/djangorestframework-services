"""Tests for views/utils.py."""

from __future__ import annotations

from rest_framework_services.views.utils import resolve_callable_kwargs


def test_passes_only_declared_params() -> None:
    def fn(*, a: int, b: int) -> int:
        return a + b

    pool = {"a": 1, "b": 2, "c": 3}
    assert resolve_callable_kwargs(fn, pool) == {"a": 1, "b": 2}


def test_passes_everything_when_var_keyword_present() -> None:
    def fn(**kwargs: object) -> dict[str, object]:
        return kwargs

    pool = {"a": 1, "b": 2}
    assert resolve_callable_kwargs(fn, pool) == pool


def test_pool_missing_keys_simply_omits() -> None:
    def fn(*, a: int, b: int = 0) -> int:
        return a + b

    pool = {"a": 1}
    assert resolve_callable_kwargs(fn, pool) == {"a": 1}


def test_positional_or_keyword_params_resolved() -> None:
    def fn(a: int, b: int) -> int:
        return a + b

    pool = {"a": 1, "b": 2, "c": 3}
    assert resolve_callable_kwargs(fn, pool) == {"a": 1, "b": 2}


def test_var_positional_ignored() -> None:
    def fn(*args: int, b: int) -> int:
        return b + sum(args)

    pool = {"args": (1, 2), "b": 3}
    # *args is VAR_POSITIONAL, not collected as a kwarg.
    assert resolve_callable_kwargs(fn, pool) == {"b": 3}
