"""Tests for ``acall_selector``."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.test import APIRequestFactory

from rest_framework_services import acall_selector


def _build_request(*, user: Any) -> Any:
    request = APIRequestFactory().get("/")
    request.user = user
    return request


@pytest.mark.asyncio
async def test_async_selector_awaited() -> None:
    async def aselector(*, request: Any, user: Any, value: int) -> int:
        assert request is not None
        assert user == "alice"
        return value + 100

    result = await acall_selector(aselector, request=_build_request(user="alice"), value=1)
    assert result == 101


@pytest.mark.asyncio
async def test_sync_selector_called_inline() -> None:
    def selector(*, value: int) -> int:
        return value - 1

    result = await acall_selector(selector, request=_build_request(user="x"), value=5)
    assert result == 4


@pytest.mark.asyncio
async def test_extras_pass_through() -> None:
    captured: dict[str, Any] = {}

    async def aselector(*, slug: str, tenant_id: int) -> None:
        captured["slug"] = slug
        captured["tenant_id"] = tenant_id

    await acall_selector(aselector, request=_build_request(user="x"), slug="s", tenant_id=9)

    assert captured == {"slug": "s", "tenant_id": 9}


@pytest.mark.asyncio
async def test_signature_filter_drops_undeclared_keys() -> None:
    async def aselector(*, slug: str) -> str:
        return slug

    result = await acall_selector(
        aselector, request=_build_request(user="x"), slug="ok", ignored="zz"
    )
    assert result == "ok"
