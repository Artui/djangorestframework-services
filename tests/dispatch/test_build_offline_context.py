"""Tests for ``build_offline_context``."""

from __future__ import annotations

from django.http import HttpRequest

from rest_framework_services.dispatch.build_offline_context import build_offline_context
from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.offline_service_view import OfflineServiceView

_USER = object()


def test_defaults_create_a_fresh_post_request() -> None:
    ctx = build_offline_context(_USER)
    assert isinstance(ctx, OfflineContext)
    assert isinstance(ctx.view, OfflineServiceView)
    assert ctx.user is _USER
    assert ctx.request.user is _USER
    assert ctx.request.method == "POST"
    assert ctx.request.data == {}
    assert ctx.view.action is None
    assert ctx.view.kwargs == {}
    assert ctx.view.request is ctx.request


def test_mapping_params_populate_request_data() -> None:
    ctx = build_offline_context(_USER, {"name": "x", "count": 2})
    assert ctx.request.data == {"name": "x", "count": 2}


def test_list_params_populate_request_data() -> None:
    ctx = build_offline_context(_USER, [{"a": 1}, {"a": 2}])
    assert ctx.request.data == [{"a": 1}, {"a": 2}]


def test_action_and_kwargs_flow_onto_the_view() -> None:
    ctx = build_offline_context(_USER, action="orders.create", kwargs={"pk": 7})
    assert ctx.view.action == "orders.create"
    assert ctx.view.kwargs == {"pk": 7}


def test_supplied_http_request_is_wrapped_and_forced_to_post() -> None:
    base = HttpRequest()
    base.method = "GET"
    base.META["HTTP_X_CUSTOM"] = "kept"
    ctx = build_offline_context(_USER, {"a": 1}, http_request=base)
    # The synthetic DRF request wraps the one we passed in.
    assert ctx.request._request is base
    assert base.method == "POST"
    # Pre-existing META survives, so headers a real transport carries are visible.
    assert ctx.request.META["HTTP_X_CUSTOM"] == "kept"
    assert ctx.request.data == {"a": 1}
