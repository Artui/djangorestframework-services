"""Off HTTP, a single call's arguments that are no object are refused in this package's words.

A service spec without ``many`` takes one object. Sent a list, a string or a
number instead, ``dispatch_spec`` and ``adispatch_spec`` with no view refuse
it as a live view refuses such a body, naming what arrived and the one spec
that takes a list, rather than in the kernel's ``"Expected an object."``.
A selector's arguments and a ``many`` spec's rows under ``many_argument`` are
no body, and keep the kernel's words
(test_arguments_that_are_no_object_take_no_filter_data,
test_a_live_views_rows_under_an_argument_keep_the_kernels_refusal).
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from rest_framework_services import ServiceSpec
from rest_framework_services.dispatch.adispatch_spec import adispatch_spec
from rest_framework_services.dispatch.dispatch_spec import dispatch_spec
from rest_framework_services.service_specs.invalid_arguments import InvalidArguments


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


def _sync(spec: Any, **kwargs: Any) -> Any:
    return dispatch_spec(spec, user=None, **kwargs)


def _async(spec: Any, **kwargs: Any) -> Any:
    return async_to_sync(adispatch_spec)(spec, user=None, **kwargs)


_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])

_SPECS = pytest.mark.parametrize(
    "spec",
    [
        ServiceSpec(service=lambda *, data: data, input_serializer=_TitleIn, atomic=False),
        ServiceSpec(service=lambda: None, atomic=False),
    ],
    ids=["serializer", "no-serializer"],
)


@_CORES
@_SPECS
@pytest.mark.parametrize(("params", "sent"), [([{"title": "t"}], "list"), ("x", "str"), (5, "int")])
def test_it_names_what_arrived(dispatch: Any, spec: Any, params: Any, sent: str) -> None:
    with pytest.raises(InvalidArguments) as excinfo:
        dispatch(spec, params=params)

    assert excinfo.value.detail == {
        "non_field_errors": [
            f"Invalid data. Expected a dictionary, but got {sent}. "
            "Only a spec declaring many=True accepts a list payload."
        ]
    }
    assert excinfo.value.get_codes() == {"non_field_errors": ["invalid"]}
    assert isinstance(excinfo.value, ValidationError)
