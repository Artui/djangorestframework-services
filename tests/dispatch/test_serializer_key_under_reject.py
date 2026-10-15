"""A caller's ``serializer`` key is dropped, and an input field named ``serializer`` is the serializer's.

The Validator binds ``serializer`` for the call, so a caller sending one names
what the call fills itself: 0.57.0 dropped it under ``UnknownArguments.REJECT``
rather than refuse it, as it dropped ``request``. A serializer was always free
to declare a field of that name: the client's value was validated into
``data``, refused in the serializer's words when missing, and the service's
``serializer`` stayed the bound one. Both cores, through the public
signatures, and a live view.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    DispatchResult,
    SelectorSpec,
    ServiceCreateView,
    ServiceSpec,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.unknown_arguments import UnknownArguments


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(spec, **kwargs)


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch_with", [_sync, _async], ids=["sync", "async"])
_POLICIES = pytest.mark.parametrize(
    "policy", [UnknownArguments.REJECT, UnknownArguments.IGNORE], ids=["reject", "ignore"]
)


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


class _SerializerFieldIn(serializers.Serializer):
    serializer = serializers.CharField()
    title = serializers.CharField()


def _reject(dispatch_with: Dispatch, spec: Any, params: Any) -> DispatchResult:
    return dispatch_with(spec, user=None, params=params, unknown_arguments=UnknownArguments.REJECT)


@_CORES
def test_a_selector_callers_serializer_key_is_dropped(dispatch_with: Dispatch) -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda *, pk: pk)

    result = _reject(dispatch_with, spec, {"pk": 1, "serializer": "spoofed"})

    assert result.value == 1


@pytest.mark.django_db
@_CORES
@pytest.mark.parametrize("serializer", [_TitleIn, None], ids=["validated", "no-serializer"])
def test_a_service_callers_serializer_key_is_dropped(
    dispatch_with: Dispatch, serializer: Any
) -> None:
    spec = ServiceSpec(
        service=lambda *, data: dict(data), input_serializer=serializer, atomic=False
    )
    params = {"title": "t"} if serializer is not None else {}

    result = _reject(dispatch_with, spec, {**params, "serializer": "spoofed"})

    assert result.value == params


@pytest.mark.django_db
@_CORES
def test_a_rows_serializer_key_is_dropped(dispatch_with: Dispatch) -> None:
    spec = ServiceSpec(
        service=lambda *, data: [dict(row) for row in data],
        input_serializer=_TitleIn,
        many=True,
        atomic=False,
    )

    result = _reject(dispatch_with, spec, [{"title": "a", "serializer": "spoofed"}])

    assert result.value == [{"title": "a"}]


def _sees_the_bound_serializer(*, data: Any, serializer: Any) -> tuple[dict[str, Any], str]:
    return dict(data), type(serializer).__name__


@pytest.mark.django_db
@_CORES
@_POLICIES
def test_an_input_field_named_serializer_validates_into_data(
    dispatch_with: Dispatch, policy: UnknownArguments
) -> None:
    spec = ServiceSpec(
        service=_sees_the_bound_serializer, input_serializer=_SerializerFieldIn, atomic=False
    )

    result = dispatch_with(
        spec, user=None, params={"title": "t", "serializer": "sent"}, unknown_arguments=policy
    )

    assert result.value == ({"serializer": "sent", "title": "t"}, "_SerializerFieldIn")


@pytest.mark.django_db
@_CORES
@_POLICIES
def test_an_input_field_named_serializer_is_required_in_the_serializers_words(
    dispatch_with: Dispatch, policy: UnknownArguments
) -> None:
    spec = ServiceSpec(
        service=_sees_the_bound_serializer, input_serializer=_SerializerFieldIn, atomic=False
    )

    with pytest.raises(ValidationError) as excinfo:
        dispatch_with(spec, user=None, params={"title": "t"}, unknown_arguments=policy)

    assert excinfo.value.detail == {"serializer": ["This field is required."]}


@pytest.mark.django_db
def test_an_input_field_named_serializer_validates_over_http() -> None:
    seen: dict[str, Any] = {}

    def service(*, data: Any, serializer: Any) -> None:
        seen.update(data=dict(data), serializer=type(serializer).__name__)

    view = type(
        "_View",
        (ServiceCreateView,),
        {
            "permission_classes": [],
            "spec": ServiceSpec(service=service, input_serializer=_SerializerFieldIn, atomic=False),
        },
    )

    response = view.as_view()(
        APIRequestFactory().post("/", {"title": "t", "serializer": "sent"}, format="json")
    )

    assert response.status_code == 204, response.data
    assert seen == {
        "data": {"serializer": "sent", "title": "t"},
        "serializer": "_SerializerFieldIn",
    }
