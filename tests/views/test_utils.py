"""Tests for views/utils.py."""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from rest_framework_services.views.utils import (
    resolve_callable_kwargs,
    resolve_extra_kwargs,
)


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


_factory = APIRequestFactory()


def _request() -> Request:
    return Request(_factory.get("/"))


class _ViewWithCatchAll:
    def get_service_kwargs(self) -> dict[str, Any]:
        return {"a": "catch", "shared": "catch"}


class _ViewWithActionHook(_ViewWithCatchAll):
    def get_create_service_kwargs(self) -> dict[str, Any]:
        return {"b": "action", "shared": "action"}


class _BareView:
    pass


class TestResolveExtraKwargs:
    def test_returns_empty_when_no_layers_present(self) -> None:
        result = resolve_extra_kwargs(
            _BareView(),
            _request(),
            spec_kwargs=None,
            action_hook=None,
            catch_all_hook="get_service_kwargs",
        )
        assert result == {}

    def test_catch_all_hook_only(self) -> None:
        result = resolve_extra_kwargs(
            _ViewWithCatchAll(),
            _request(),
            spec_kwargs=None,
            action_hook=None,
            catch_all_hook="get_service_kwargs",
        )
        assert result == {"a": "catch", "shared": "catch"}

    def test_action_hook_overrides_catch_all(self) -> None:
        result = resolve_extra_kwargs(
            _ViewWithActionHook(),
            _request(),
            spec_kwargs=None,
            action_hook="get_create_service_kwargs",
            catch_all_hook="get_service_kwargs",
        )
        assert result == {"a": "catch", "b": "action", "shared": "action"}

    def test_action_hook_missing_method_falls_back(self) -> None:
        result = resolve_extra_kwargs(
            _ViewWithCatchAll(),
            _request(),
            spec_kwargs=None,
            action_hook="get_nonexistent_service_kwargs",
            catch_all_hook="get_service_kwargs",
        )
        assert result == {"a": "catch", "shared": "catch"}

    def test_spec_kwargs_overrides_action_and_catch_all(self) -> None:
        def provider(view: Any, request: Request) -> dict[str, Any]:
            return {"c": "spec", "shared": "spec"}

        result = resolve_extra_kwargs(
            _ViewWithActionHook(),
            _request(),
            spec_kwargs=provider,
            action_hook="get_create_service_kwargs",
            catch_all_hook="get_service_kwargs",
        )
        assert result == {
            "a": "catch",
            "b": "action",
            "c": "spec",
            "shared": "spec",
        }

    def test_spec_kwargs_receives_view_and_request(self) -> None:
        captured: dict[str, Any] = {}

        def provider(view: Any, request: Request) -> dict[str, Any]:
            captured["view"] = view
            captured["request"] = request
            return {}

        view = _BareView()
        request = _request()
        resolve_extra_kwargs(
            view,
            request,
            spec_kwargs=provider,
            action_hook=None,
            catch_all_hook="get_service_kwargs",
        )
        assert captured["view"] is view
        assert captured["request"] is request
