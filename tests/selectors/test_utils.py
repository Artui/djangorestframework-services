"""Tests for non-exported helpers in selectors/utils.py."""

from __future__ import annotations

import pytest

from rest_framework_services.selectors.utils import arun_selector, run_selector


def _sync_fn(*, value: int) -> int:
    return value * 2


async def _async_fn(*, value: int) -> int:
    return value * 3


class TestRunSelector:
    def test_sync(self) -> None:
        assert run_selector(_sync_fn, {"value": 5}) == 10

    def test_async_bridged(self) -> None:
        assert run_selector(_async_fn, {"value": 5}) == 15


class TestArunSelector:
    @pytest.mark.asyncio
    async def test_async(self) -> None:
        assert await arun_selector(_async_fn, {"value": 4}) == 12

    @pytest.mark.asyncio
    async def test_sync(self) -> None:
        assert await arun_selector(_sync_fn, {"value": 4}) == 8
