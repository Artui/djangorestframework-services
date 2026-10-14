"""``UnknownArguments.REJECT`` at the DRF edge, on both cores.

The kernel refuses an undeclared key itself; the edge decides when that refusal
is asked for (before any lookup queries), where the kernel can hold a caller to
it at all (not against an open callable), and which refusals it rewords into
this package's ``"Unexpected argument(s): ..."`` message (only the kernel's own).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from rest_framework_services import (
    DispatchResult,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.unknown_arguments import UnknownArguments
from tests.testapp.models import Post


def _sync(spec: Any, **kwargs: Any) -> DispatchResult:
    return dispatch_spec(spec, user=None, unknown_arguments=UnknownArguments.REJECT, **kwargs)


def _async(spec: Any, **kwargs: Any) -> DispatchResult:
    return async_to_sync(adispatch_spec)(
        spec, user=None, unknown_arguments=UnknownArguments.REJECT, **kwargs
    )


Dispatch = Callable[..., DispatchResult]
_CORES = pytest.mark.parametrize("dispatch", [_sync, _async], ids=["sync", "async"])


# --- before the lookup ------------------------------------------------------------

_LOOKED_UP: list[int] = []


def _post_by_pk(*, pk: int) -> QuerySet[Post]:
    _LOOKED_UP.append(pk)
    return Post.objects.filter(pk=pk)


def _touch(*, instance: Post) -> int:
    return instance.pk


_TOUCH = ServiceSpec(
    service=_touch,
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk),
    atomic=False,
)


@_CORES
@pytest.mark.django_db
def test_an_unknown_key_is_refused_before_the_lookup_asks_the_database(
    dispatch: Dispatch, django_assert_num_queries: Any
) -> None:
    """A missing row beside an undeclared key is the caller's 400, as it always
    was, and the database is not asked for the row."""
    _LOOKED_UP.clear()

    with django_assert_num_queries(0), pytest.raises(ValidationError) as excinfo:
        dispatch(_TOUCH, params={"pk": 404, "bogus": 1})

    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'bogus'."]}
    assert _LOOKED_UP == []


@_CORES
@pytest.mark.django_db
def test_a_missing_row_with_no_unknown_key_is_still_not_found(dispatch: Dispatch) -> None:
    result = dispatch(_TOUCH, params={"pk": 404})

    assert result.kind == "not_found"


class _TitleIn(serializers.Serializer):
    title = serializers.CharField()


def _retitle(*, instance: Post, data: Any) -> int:
    return instance.pk


_RETITLE = ServiceSpec(
    service=_retitle,
    input_serializer=_TitleIn,
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_post_by_pk),
    atomic=False,
)


@_CORES
@pytest.mark.django_db
def test_a_missing_row_and_a_missing_field_is_still_not_found(dispatch: Dispatch) -> None:
    """Only an unknown key is answered ahead of the lookup: a field the body left
    out is the Validator's to refuse, once there is a row to refuse it for."""
    result = dispatch(_RETITLE, params={"pk": 404})

    assert result.kind == "not_found"


@_CORES
@pytest.mark.django_db
def test_an_unknown_key_ahead_of_the_lookup_is_refused_alone(dispatch: Dispatch) -> None:
    """The refusal names the unknown key only, as it did when it ran first: a
    field left out or sent wrong is not reported beside it."""
    with pytest.raises(ValidationError) as excinfo:
        dispatch(_RETITLE, params={"pk": 404, "bogus": 1})

    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'bogus'."]}


# --- an open service --------------------------------------------------------------


def _open(**changes: Any) -> dict[str, Any]:
    return changes


def _closed(*, title: str) -> str:
    return title


@_CORES
@pytest.mark.django_db
def test_an_open_spreading_service_takes_an_undeclared_key_and_drops_it(
    dispatch: Dispatch,
) -> None:
    """A service with no serializer taking a bare ``**kwargs``, spread: nothing
    declares a set to hold the caller to, as an open selector declares none."""
    spec = ServiceSpec(service=_open, atomic=False)

    result = dispatch(
        spec, params={"anything": 1}, argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS
    )

    assert "anything" not in result.service_result


@_CORES
@pytest.mark.django_db
def test_an_open_service_is_held_to_its_set_when_it_does_not_spread(dispatch: Dispatch) -> None:
    spec = ServiceSpec(service=_open, atomic=False)

    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params={"anything": 1})

    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'anything'."]}


@_CORES
@pytest.mark.django_db
def test_an_open_spreading_service_with_a_serializer_is_held_to_its_fields(
    dispatch: Dispatch,
) -> None:
    spec = ServiceSpec(service=_open, input_serializer=_TitleIn, atomic=False)

    with pytest.raises(ValidationError) as excinfo:
        dispatch(
            spec,
            params={"title": "t", "anything": 1},
            argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        )

    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'anything'."]}


@_CORES
@pytest.mark.django_db
def test_a_closed_spreading_service_is_held_to_its_parameters(dispatch: Dispatch) -> None:
    spec = ServiceSpec(service=_closed, atomic=False)

    with pytest.raises(ValidationError) as excinfo:
        dispatch(
            spec,
            params={"title": "t", "anything": 1},
            argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        )

    assert excinfo.value.detail == {"non_field_errors": ["Unexpected argument(s): 'anything'."]}


def _unresolvable(**changes: Unresolvable) -> dict[str, Any]:  # noqa: F821
    return changes


@_CORES
@pytest.mark.django_db
def test_an_unresolvable_spreading_service_cannot_be_held_to_reject(dispatch: Dispatch) -> None:
    spec = ServiceSpec(service=_unresolvable, atomic=False)

    with pytest.raises(ImproperlyConfigured, match="REJECT cannot be enforced"):
        dispatch(spec, params={}, argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS)


# --- only the kernel's refusal is reworded ----------------------------------------


class _RefusesWithTheUnknownCode(serializers.Serializer):
    title = serializers.CharField()

    def validate_title(self, value: str) -> str:
        if value == "field":
            raise serializers.ValidationError("No such title.", code="unknown")
        return value

    def validate(self, attrs: Any) -> Any:
        if attrs["title"] == "object":
            raise serializers.ValidationError("No such thing.", code="unknown")
        return attrs


def _echo(*, data: Any) -> Any:
    return data


@_CORES
@pytest.mark.django_db
@pytest.mark.parametrize(
    ("title", "detail"),
    [
        ("field", {"title": ["No such title."]}),
        ("object", {"non_field_errors": ["No such thing."]}),
    ],
)
def test_a_validators_unknown_code_is_left_as_it_raised_it(
    dispatch: Dispatch, title: str, detail: dict[str, Any]
) -> None:
    spec = ServiceSpec(service=_echo, input_serializer=_RefusesWithTheUnknownCode, atomic=False)

    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params={"title": title})

    assert excinfo.value.detail == detail
    assert excinfo.value.get_codes() == {next(iter(detail)): ["unknown"]}


@_CORES
@pytest.mark.django_db
def test_a_validators_unknown_code_inside_a_row_is_left_as_it_raised_it(
    dispatch: Dispatch,
) -> None:
    spec = ServiceSpec(
        service=_echo, input_serializer=_RefusesWithTheUnknownCode, many=True, atomic=False
    )

    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params=[{"title": "ok"}, {"title": "field"}])

    assert excinfo.value.detail == [{}, {"title": ["No such title."]}]


@_CORES
@pytest.mark.django_db
def test_an_unknown_key_beside_a_validators_refusal_of_a_row_is_reworded_alone(
    dispatch: Dispatch,
) -> None:
    spec = ServiceSpec(
        service=_echo, input_serializer=_RefusesWithTheUnknownCode, many=True, atomic=False
    )

    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params=[{"title": "ok", "bogus": 1}])

    assert excinfo.value.detail == [{"non_field_errors": ["Unexpected argument(s): 'bogus'."]}]


class _RefusesElsewhere(serializers.Serializer):
    title = serializers.CharField()

    def validate(self, attrs: Any) -> Any:
        raise serializers.ValidationError({"elsewhere": ["Not here."]})


@_CORES
@pytest.mark.django_db
def test_a_validators_refusal_under_an_undeclared_key_keeps_its_key(dispatch: Dispatch) -> None:
    spec = ServiceSpec(service=_echo, input_serializer=_RefusesElsewhere, atomic=False)

    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params={"title": "t"})

    assert excinfo.value.detail == {"elsewhere": ["Not here."]}


class _Geo(serializers.Serializer):
    lat = serializers.FloatField()


class _Address(serializers.Serializer):
    geo = _Geo()


class _Located(serializers.Serializer):
    address = _Address()


@_CORES
@pytest.mark.django_db
def test_a_refusal_two_objects_deep_is_left_as_raised(dispatch: Dispatch) -> None:
    """Only rows are read as a level of their own: a nested object's refusal,
    however deep, is not."""
    spec = ServiceSpec(service=_echo, input_serializer=_Located, atomic=False)

    with pytest.raises(ValidationError) as excinfo:
        dispatch(spec, params={"address": {"geo": {}}})

    assert excinfo.value.get_codes() == {"address": {"geo": {"lat": ["required"]}}}
