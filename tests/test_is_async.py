"""Tests for ``is_async`` — the callable-kind predicate."""

from __future__ import annotations

import functools

from rest_framework_services.is_async import is_async


class _AsyncCallable:
    async def __call__(self) -> str:
        return "async-callable"


class _SyncCallable:
    def __call__(self) -> str:
        return "sync-callable"


async def _async_fn() -> int:
    return 1


def _sync_fn() -> int:
    return 1


class TestIsAsync:
    def test_async_def(self) -> None:
        assert is_async(_async_fn) is True

    def test_sync_def(self) -> None:
        assert is_async(_sync_fn) is False

    def test_async_callable_class(self) -> None:
        assert is_async(_AsyncCallable()) is True

    def test_sync_callable_class(self) -> None:
        assert is_async(_SyncCallable()) is False

    def test_partial_of_async(self) -> None:
        partial = functools.partial(_async_fn)
        assert is_async(partial) is True

    def test_partial_of_sync(self) -> None:
        partial = functools.partial(_sync_fn)
        assert is_async(partial) is False

    def test_lambda(self) -> None:
        assert is_async(lambda: 1) is False

    def test_object_without_call(self) -> None:
        class _NoCall: ...

        # `_NoCall` instances are not callable, but is_async should still return False.
        assert is_async(_NoCall()) is False
