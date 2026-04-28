"""Test that ``ServiceView`` Protocol is structurally usable."""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from rest_framework_services import ServiceView


class _Stand:
    """Standalone view shape: action is None."""

    def __init__(self) -> None:
        self.request: Request = Request(APIRequestFactory().get("/"))
        self.kwargs: dict[str, Any] = {}
        self.action: str | None = None


class _Viewset:
    """Viewset shape: action is set."""

    def __init__(self) -> None:
        self.request: Request = Request(APIRequestFactory().get("/x/7/"))
        self.kwargs: dict[str, Any] = {"pk": 7}
        self.action: str | None = "retrieve"


def _provider(view: ServiceView, request: Request) -> dict[str, Any]:
    return {"action": view.action, "url_kwargs": dict(view.kwargs)}


def test_provider_can_consume_standalone_view() -> None:
    view = _Stand()
    out = _provider(view, view.request)
    assert out == {"action": None, "url_kwargs": {}}


def test_provider_can_consume_viewset_view() -> None:
    view = _Viewset()
    out = _provider(view, view.request)
    assert out == {"action": "retrieve", "url_kwargs": {"pk": 7}}
