"""Tests for the Selector + AsyncSelector protocols."""

from __future__ import annotations

from rest_framework_services.selectors import AsyncSelector, Selector


def test_selector_runtime_check_accepts_callable() -> None:
    def fn(**kwargs: object) -> int:
        return 1

    assert isinstance(fn, Selector)


def test_selector_runtime_check_rejects_non_callable() -> None:
    assert not isinstance(123, Selector)


def test_async_selector_runtime_check_accepts_callable() -> None:
    async def fn(**kwargs: object) -> int:
        return 1

    assert isinstance(fn, AsyncSelector)
