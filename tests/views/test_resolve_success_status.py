"""Unit tests for ``resolve_success_status``."""

from __future__ import annotations

from typing import Any

from rest_framework_services.views.mutation.resolve_success_status import resolve_success_status


def test_none_falls_back_to_default() -> None:
    assert resolve_success_status(None, default=201, pool={}) == 201


def test_int_is_returned_verbatim() -> None:
    # An explicit int wins over the default, ignoring the pool.
    assert resolve_success_status(202, default=201, pool={"result": object()}) == 202


def test_callable_resolves_through_pool() -> None:
    def status(*, result: dict[str, Any]) -> int:
        return 200 if result["existing"] else 201

    assert resolve_success_status(status, default=500, pool={"result": {"existing": True}}) == 200
    assert resolve_success_status(status, default=500, pool={"result": {"existing": False}}) == 201


def test_callable_receives_only_declared_pool_keys() -> None:
    seen: dict[str, Any] = {}

    def status(*, instance: Any, request: Any) -> int:
        seen["instance"] = instance
        seen["request"] = request
        return 207

    pool = {"result": object(), "instance": "row", "request": "req", "view": "view"}
    assert resolve_success_status(status, default=200, pool=pool) == 207
    # ``result`` / ``view`` are in the pool but not declared, so not passed.
    assert seen == {"instance": "row", "request": "req"}


def test_callable_with_var_keyword_gets_whole_pool() -> None:
    def status(**pool: Any) -> int:
        return len(pool)

    assert resolve_success_status(status, default=0, pool={"result": 1, "view": 2}) == 2


def test_callable_tolerates_absent_pool_keys() -> None:
    # ``instance`` is absent (create / bulk path); a callable declaring it with
    # a default still resolves.
    def status(*, result: Any, instance: Any = None) -> int:
        return 201 if instance is None else 200

    assert resolve_success_status(status, default=500, pool={"result": object()}) == 201
