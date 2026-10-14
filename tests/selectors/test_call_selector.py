"""Tests for ``call_selector``."""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from rest_framework_services import call_selector


def _build_request(*, user: Any) -> Any:
    request = APIRequestFactory().get("/")
    request.user = user
    return request


def test_passes_request_and_user() -> None:
    captured: dict[str, Any] = {}

    def selector(*, request: Any, user: Any) -> list[int]:
        captured["request"] = request
        captured["user"] = user
        return [1, 2, 3]

    request = _build_request(user="alice")
    result = call_selector(selector, request=request)

    assert result == [1, 2, 3]
    assert captured["request"] is request
    assert captured["user"] == "alice"


def test_user_is_none_when_request_lacks_user_attr() -> None:
    class _BareRequest: ...

    captured: dict[str, Any] = {}

    def selector(*, user: Any) -> None:
        captured["user"] = user

    call_selector(selector, request=_BareRequest())  # type: ignore[arg-type]

    assert captured["user"] is None


def test_extras_merge_into_pool() -> None:
    captured: dict[str, Any] = {}

    def selector(*, tenant_id: int, slug: str) -> None:
        captured["tenant_id"] = tenant_id
        captured["slug"] = slug

    call_selector(selector, request=_build_request(user="x"), tenant_id=1, slug="s")

    assert captured == {"tenant_id": 1, "slug": "s"}


def test_signature_filter_drops_undeclared_keys() -> None:
    def selector(*, slug: str) -> str:
        return slug

    result = call_selector(selector, request=_build_request(user="x"), slug="hello")
    assert result == "hello"


def test_async_selector_bridged_via_async_to_sync() -> None:
    async def aselector(*, value: int) -> int:
        return value * 4

    assert call_selector(aselector, request=_build_request(user="x"), value=3) == 12


def test_the_view_a_drf_request_carries_is_seeded() -> None:
    """The framework seeds ``view`` beside ``request``, so the helper does too,
    from the view DRF records on the request it builds."""
    seen: dict[str, Any] = {}
    view = object()

    def selector(*, view: Any) -> int:
        seen["view"] = view
        return 1

    request = Request(APIRequestFactory().get("/"), parser_context={"view": view})
    assert call_selector(selector, request=request) == 1
    assert seen["view"] is view


def test_a_request_carrying_no_view_leaves_the_name_to_the_caller() -> None:
    """Off a view (a plain ``HttpRequest``), nothing is seeded, so a ``view`` the
    caller passes reaches the callable."""
    seen: dict[str, Any] = {}

    def selector(*, view: Any) -> int:
        seen["view"] = view
        return 1

    assert call_selector(selector, request=APIRequestFactory().get("/"), view="mine") == 1
    assert seen["view"] == "mine"


def test_a_view_extra_replaces_the_recorded_view() -> None:
    """Unlike ``call_service``, the selector helper spreads ``extras`` last, so a
    ``view`` key in them replaces the recorded view, as a ``user`` key does."""
    seen: dict[str, Any] = {}

    def selector(*, view: Any) -> int:
        seen["view"] = view
        return 1

    request = Request(APIRequestFactory().get("/"), parser_context={"view": object()})
    assert call_selector(selector, request=request, view="mine") == 1
    assert seen["view"] == "mine"
