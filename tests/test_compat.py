"""Tests for the _compat package: is_async, run_service, arun_service."""

from __future__ import annotations

import functools
from typing import Any

import pytest
from django.db import IntegrityError, transaction

from rest_framework_services._compat import arun_service, is_async, run_service
from tests.testapp.models import Author


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
        class _NoCall:
            pass

        # `_NoCall` instances are not callable, but is_async should still return False.
        assert is_async(_NoCall()) is False


@pytest.mark.django_db(transaction=True)
class TestRunServiceSync:
    def test_passes_kwargs(self) -> None:
        def fn(*, x: int, y: int) -> int:
            return x + y

        assert run_service(fn, {"x": 2, "y": 3}, atomic=False) == 5

    def test_no_atomic_no_transaction(self) -> None:
        captured: dict[str, bool] = {}

        def fn() -> None:
            captured["in_atomic"] = transaction.get_connection().in_atomic_block

        run_service(fn, {}, atomic=False)
        assert captured["in_atomic"] is False

    def test_atomic_wraps(self) -> None:
        captured: dict[str, bool] = {}

        def fn() -> None:
            captured["in_atomic"] = transaction.get_connection().in_atomic_block

        run_service(fn, {}, atomic=True)
        assert captured["in_atomic"] is True

    def test_atomic_rollback_on_exception(self) -> None:
        Author.objects.create(name="seed")

        def fn() -> None:
            Author.objects.create(name="will-rollback")
            raise IntegrityError("forced rollback")

        with pytest.raises(IntegrityError):
            run_service(fn, {}, atomic=True)

        assert Author.objects.count() == 1

    def test_returns_value(self) -> None:
        def fn() -> str:
            return "ok"

        assert run_service(fn, {}, atomic=True) == "ok"


@pytest.mark.django_db(transaction=True)
class TestArunService:
    async def test_passes_kwargs_no_atomic(self) -> None:
        async def fn(*, value: int) -> int:
            return value * 2

        assert await arun_service(fn, {"value": 7}, atomic=False) == 14

    async def test_returns_value_atomic(self) -> None:
        async def fn() -> str:
            return "ok"

        assert await arun_service(fn, {}, atomic=True) == "ok"

    async def test_atomic_creates_persist(self) -> None:
        async def fn() -> Any:
            return await Author.objects.acreate(name="persisted")

        author = await arun_service(fn, {}, atomic=True)
        assert author.pk is not None
        assert await Author.objects.acount() == 1

    async def test_atomic_rolls_back_on_error(self) -> None:
        async def fn() -> None:
            await Author.objects.acreate(name="oops")
            raise IntegrityError("boom")

        with pytest.raises(IntegrityError):
            await arun_service(fn, {}, atomic=True)

        assert await Author.objects.acount() == 0
