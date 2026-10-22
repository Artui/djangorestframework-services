"""``view_call_context``: the one call-context dict of the request a view serves.

The live-view cases in ``tests/test_transport_parity.py`` hold the hand-offs a
request makes; these hold the helper's own contract, and the layers of the
view-side resolvers that only a direct call reaches (the spec's own ``kwargs``
and ``input_data`` layers, which ``dispatch_spec`` runs on a live view).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from rest_framework_services import ServiceSpec, ServiceViewSet
from rest_framework_services.views.utils import (
    resolve_extra_kwargs,
    resolve_input_extras,
    resolve_serializer_context,
    view_call_context,
)


class _View:
    action = "create"

    def get_serializer_context(self) -> dict[str, Any]:
        return {}


def _request() -> Request:
    return Request(APIRequestFactory().get("/"))


def test_the_dict_carries_the_edge_s_four_standard_entries() -> None:
    view, request = _View(), _request()
    assert view_call_context(view, request) == {
        "request": request,
        "view": view,
        "format": None,
        "action": "create",
    }


def test_a_view_with_no_action_is_a_call_with_none() -> None:
    view = _View()
    view.action = ""
    assert view_call_context(view, _request())["action"] is None


def test_one_view_is_one_dict_and_another_view_another() -> None:
    view, request = _View(), _request()
    assert view_call_context(view, request) is view_call_context(view, request)
    assert view_call_context(_View(), request) is not view_call_context(view, request)


def _recording(seen: list[Any]) -> Any:
    def provide(*, call_context: dict[str, Any]) -> dict[str, Any]:
        seen.append(call_context)
        return {}

    return provide


def test_the_spec_kwargs_layer_is_handed_the_view_s_dict() -> None:
    view, request, seen = _View(), _request(), []
    resolve_extra_kwargs(
        view,
        request,
        spec_kwargs=_recording(seen),
        action_hook=None,
        catch_all_hook="get_service_kwargs",
    )
    assert seen == [view_call_context(view, request)]
    assert seen[0] is view_call_context(view, request)


def test_the_spec_input_data_layer_is_handed_the_view_s_dict() -> None:
    view, request, seen = _View(), _request(), []
    resolve_input_extras(
        view,
        request,
        spec_input_data=_recording(seen),
        action_hook=None,
        catch_all_hook="get_input_data",
    )
    assert seen[0] is view_call_context(view, request)


def test_the_spec_serializer_context_layer_is_handed_the_view_s_dict() -> None:
    view, request, seen = _View(), _request(), []
    resolve_serializer_context(
        view,
        request,
        direction_hook="get_output_serializer_context",
        action_hook=None,
        spec_provider=_recording(seen),
    )
    assert seen[0] is view_call_context(view, request)


class _Title(serializers.Serializer):
    title = serializers.CharField()


@pytest.mark.parametrize("field", ["success_status", "response_finalizer"])
def test_a_status_or_finalizer_naming_the_call_context_is_refused_when_the_view_is_built(
    field: str,
) -> None:
    """Neither is offered the request's dict, so naming it is refused up front, not at a request."""

    def naming(*, call_context: dict[str, Any]) -> Any: ...

    spec = ServiceSpec(service=lambda *, data: data, input_serializer=_Title, **{field: naming})
    viewset = type("_VS", (ServiceViewSet,), {"action_specs": {"create": spec}})

    with pytest.raises(ImproperlyConfigured, match=r"\['call_context'\]"):
        viewset.as_view({"post": "create"})
