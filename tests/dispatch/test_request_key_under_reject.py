"""Under ``REJECT`` a caller's ``request`` key is dropped, as every seed's name is.

The edge seeds ``request`` from what it is handed, so a caller sending one names
a seed: 0.57.0 dropped it under ``UnknownArguments.REJECT`` rather than refuse
it, and the callable declaring ``request`` got the call's. ``view`` is the one
seed a caller sending is refused for, and still is. Both cores, through the
public signatures.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from rest_framework_services import (
    DispatchResult,
    SelectorSpec,
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
_REQUEST = object()


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


def _reject(dispatch_with: Dispatch, spec: Any, params: Any) -> DispatchResult:
    return dispatch_with(
        spec,
        user=None,
        params=params,
        request=_REQUEST,
        unknown_arguments=UnknownArguments.REJECT,
    )


@_CORES
def test_a_selector_callers_request_key_is_dropped(dispatch_with: Dispatch) -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda *, pk, request: (pk, request))

    result = _reject(dispatch_with, spec, {"pk": 1, "request": "spoofed"})

    assert result.value == (1, _REQUEST)


@_CORES
@pytest.mark.parametrize("serializer", [_TitleIn, None], ids=["validated", "no-serializer"])
def test_a_service_callers_request_key_is_dropped(dispatch_with: Dispatch, serializer: Any) -> None:
    spec = ServiceSpec(
        service=lambda *, data, request: (dict(data), request),
        input_serializer=serializer,
        atomic=False,
    )
    params = {"title": "t"} if serializer is not None else {}

    result = _reject(dispatch_with, spec, {**params, "request": "spoofed"})

    assert result.value == (params, _REQUEST)


@_CORES
def test_a_rows_request_key_is_dropped(dispatch_with: Dispatch) -> None:
    spec = ServiceSpec(
        service=lambda *, data, request: ([dict(row) for row in data], request),
        input_serializer=_TitleIn,
        many=True,
        atomic=False,
    )

    result = _reject(dispatch_with, spec, [{"title": "a", "request": "spoofed"}])

    assert result.value == ([{"title": "a"}], _REQUEST)


@_CORES
def test_a_callers_view_key_is_still_refused(dispatch_with: Dispatch) -> None:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda *, pk: pk)

    with pytest.raises(ValidationError) as excinfo:
        _reject(dispatch_with, spec, {"pk": 1, "view": "spoofed"})

    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'view'."]}
