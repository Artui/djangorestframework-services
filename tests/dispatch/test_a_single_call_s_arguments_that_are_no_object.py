"""Off HTTP, a single call's arguments that are no object are refused in this package's words.

A service spec without ``many`` takes one object. Sent a list, a string or a
number instead, ``dispatch_spec`` and ``adispatch_spec`` with no view refuse
it as a live view refuses such a body, naming what arrived and the one spec
that takes a list, rather than in the kernel's ``"Expected an object."``.
A selector's arguments are its query string, and keep the kernel's words
(test_arguments_that_are_no_object_take_no_filter_data). A ``many`` spec's
argument object that is no object is refused in DRF's words too
(test_arguments_that_are_not_an_object_are_refused).
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from rest_framework_services import SelectorKind, SelectorSpec, ServiceSpec
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


class _Numbers(serializers.Serializer):
    items = serializers.ListField(child=serializers.IntegerField())


@_CORES
def test_a_list_field_named_like_many_argument_keeps_its_rows_own_refusal(dispatch: Any) -> None:
    """A single call sends no rows, whatever its field named like ``many_argument``
    holds, so an entry of that field is never refused as a row that is no object."""
    spec = ServiceSpec(service=lambda *, data: data, input_serializer=_Numbers, atomic=False)
    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params={"items": [1, "x"]})

    entry = excinfo.value.detail["items"][1]
    assert isinstance(entry, list)
    assert "Expected a dictionary" not in str(excinfo.value.detail)


@_CORES
@pytest.mark.parametrize("params", [["a"], 5], ids=["list", "int"])
def test_a_selector_spec_given_arguments_that_are_no_object_is_refused(
    dispatch: Any, params: Any
) -> None:
    """Refused as the kernel refuses them, where 0.57.0 raised ``AttributeError``
    for a list and ``TypeError`` for a number."""
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=lambda: [], permission_classes=[])
    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params=params)

    assert excinfo.value.detail == {"non_field_errors": ["Expected an object."]}
