"""Unit tests for ``apply_response_finalizer``."""

from __future__ import annotations

from typing import Any

from rest_framework.response import Response

from rest_framework_services.views.mutation.apply_response_finalizer import (
    apply_response_finalizer,
)


def _apply(finalizer: Any, **kwargs: Any) -> Response:
    base = kwargs.pop("response", Response({"ok": True}, status=200))
    return apply_response_finalizer(
        finalizer,
        base,
        request=kwargs.pop("request", object()),
        view=kwargs.pop("view", object()),
        result=kwargs.pop("result", None),
        **kwargs,
    )


def test_none_finalizer_returns_response_unchanged() -> None:
    response = Response(status=204)
    assert _apply(None, response=response) is response


def test_finalizer_returning_none_keeps_response() -> None:
    response = Response({"ok": True}, status=200)

    def finalizer(*, response: Response) -> None:
        response["X-Test"] = "1"
        return None

    out = _apply(finalizer, response=response)
    assert out is response
    assert out["X-Test"] == "1"


def test_finalizer_can_swap_the_response() -> None:
    replacement = Response({"swapped": True}, status=299)

    def finalizer(**_: Any) -> Response:
        return replacement

    assert _apply(finalizer) is replacement


def test_pool_offers_result_request_view_response() -> None:
    seen: dict[str, Any] = {}
    request, view = object(), object()

    def finalizer(*, response: Response, result: Any, request: Any, view: Any) -> None:
        seen.update(response=response, result=result, request=request, view=view)
        return None

    base = Response(status=200)
    _apply(finalizer, response=base, result="R", request=request, view=view)
    assert seen == {"response": base, "result": "R", "request": request, "view": view}


def test_instance_and_data_offered_only_when_present() -> None:
    seen: dict[str, Any] = {}

    def finalizer(**pool: Any) -> None:
        seen.update(pool)
        return None

    _apply(finalizer, instance="row", data={"k": 1})
    assert seen["instance"] == "row"
    assert seen["data"] == {"k": 1}

    seen.clear()
    _apply(finalizer)  # instance / data default to None → gated out
    assert "instance" not in seen
    assert "data" not in seen
