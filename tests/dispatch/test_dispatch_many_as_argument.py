"""``many_as_argument`` -- a ``many=True`` list read out of an object of named arguments.

A caller whose input is always a JSON object cannot send the bare array a
``many=True`` spec validates over HTTP. With ``many_as_argument=True`` the list
arrives under ``spec.many_argument`` instead, and every validation error is keyed
under that argument in one shape on every supported DRF: the shape the documented
workaround, ``items = Item(many=True)`` on a wrapper serializer, produces on
current DRF.

Every case runs through both cores, because the flag is threaded through each.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from rest_framework_services import (
    ArgumentBinding,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    ServiceValidationError,
    UnknownArguments,
    adispatch_spec,
    dispatch_spec,
)
from rest_framework_services.types.dispatch_result import DispatchResult

_CORES = pytest.mark.parametrize("core", ["sync", "async"])


async def _dispatch(core: str, spec: Any, **kwargs: Any) -> DispatchResult:
    if core == "sync":
        return await sync_to_async(dispatch_spec, thread_sensitive=True)(spec, **kwargs)
    return await adispatch_spec(spec, **kwargs)


async def _refusal(core: str, spec: Any, **kwargs: Any) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        await _dispatch(core, spec, user=None, **kwargs)
    return caught.value


class _Line(serializers.Serializer):
    sku = serializers.CharField()
    quantity = serializers.IntegerField()


class _NonEmptyLines(serializers.ListSerializer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_empty", False)
        super().__init__(*args, **kwargs)


class _NonEmptyLine(_Line):
    class Meta:
        list_serializer_class = _NonEmptyLines


class _BoundedLines(serializers.ListSerializer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("min_length", 2)
        kwargs.setdefault("max_length", 3)
        super().__init__(*args, **kwargs)


class _BoundedLine(_Line):
    class Meta:
        list_serializer_class = _BoundedLines


class _ItemErrorsAsAList(serializers.ListSerializer):
    """Reports item errors as DRF below 3.18 does, in DRF 3.14's own loop: a list with
    an empty entry for every valid item. The locked DRF never produces that shape, so
    without this the normalisation would be held only by the floor job.
    ``child.run_validation`` rather than ``run_child_validation``, which 3.14 lacks."""

    def to_internal_value(self, data: Any) -> Any:
        child: Any = self.child
        ret: list[Any] = []
        errors: list[Any] = []
        for item in data:
            try:
                validated = child.run_validation(item)
            except ValidationError as exc:
                errors.append(exc.detail)
            else:
                ret.append(validated)
                errors.append({})
        if any(errors):
            raise ValidationError(errors)
        return ret


class _LineReportedAsAList(_Line):
    class Meta:
        list_serializer_class = _ItemErrorsAsAList


# The documented workaround each case is measured against.
class _Lines(serializers.Serializer):
    items = _Line(many=True)


class _NonEmptyLinesInput(serializers.Serializer):
    items = _NonEmptyLine(many=True)


def _echo(*, data: Any) -> Any:
    return data


def _plain(data: Any) -> Any:
    return [dict(item) for item in data]


def _spec(serializer: type = _Line, **overrides: Any) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=_echo, input_serializer=serializer, many=True, atomic=False, **overrides
    )


_GOOD: dict[str, Any] = {"sku": "a", "quantity": 1}
_BAD: dict[str, Any] = {"sku": "", "quantity": "x"}
_BAD_DETAIL: dict[str, Any] = {
    "sku": ["This field may not be blank."],
    "quantity": ["A valid integer is required."],
}


# --- the list reaches the service -------------------------------------------


@_CORES
async def test_the_list_under_the_default_argument_reaches_the_service(core: str) -> None:
    result = await _dispatch(
        core, _spec(), user=None, params={"items": [_GOOD, _GOOD]}, many_as_argument=True
    )
    assert result.kind == "list"
    assert _plain(result.value) == [_GOOD, _GOOD]


@_CORES
async def test_a_declared_argument_is_the_one_read(core: str) -> None:
    result = await _dispatch(
        core,
        _spec(many_argument="lines"),
        user=None,
        params={"lines": [_GOOD]},
        many_as_argument=True,
    )
    assert _plain(result.value) == [_GOOD]


@_CORES
async def test_without_the_flag_the_same_spec_still_takes_a_bare_list(core: str) -> None:
    result = await _dispatch(core, _spec(), user=None, params=[_GOOD])
    assert _plain(result.value) == [_GOOD]


@_CORES
async def test_without_the_flag_an_argument_object_is_still_not_a_list(core: str) -> None:
    """The HTTP body is not widened: only a caller that says so gets the unwrap."""
    refusal = await _refusal(core, _spec(), params={"items": [_GOOD]})
    assert refusal.detail == {"non_field_errors": ['Expected a list of items but got type "dict".']}


@_CORES
async def test_the_flag_is_inert_on_a_single_item_spec(core: str) -> None:
    spec = ServiceSpec(service=_echo, input_serializer=_Line, atomic=False)
    result = await _dispatch(core, spec, user=None, params=_GOOD, many_as_argument=True)
    assert dict(result.value) == _GOOD


@_CORES
async def test_the_flag_is_inert_on_a_selector(core: str) -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=lambda *, tag: [tag, tag])
    result = await _dispatch(core, spec, user=None, params={"tag": "x"}, many_as_argument=True)
    assert result.value == ["x", "x"]


@_CORES
async def test_input_data_merges_into_each_item_of_the_list_read(core: str) -> None:
    spec = _spec(input_data=lambda: {"quantity": 9})
    result = await _dispatch(
        core,
        spec,
        user=None,
        params={"items": [{"sku": "a"}, {"sku": "b", "quantity": 1}]},
        many_as_argument=True,
    )
    assert _plain(result.value) == [{"sku": "a", "quantity": 9}, {"sku": "b", "quantity": 9}]


@_CORES
async def test_without_an_input_serializer_the_list_still_passes_through(core: str) -> None:
    spec = ServiceSpec(service=_echo, many=True, atomic=False)
    result = await _dispatch(
        core,
        spec,
        user=None,
        params={"items": [{"note": "a"}]},
        many_as_argument=True,
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )
    assert _plain(result.value) == [{"note": "a"}]


@_CORES
async def test_without_an_input_serializer_a_non_list_is_still_refused(core: str) -> None:
    spec = ServiceSpec(service=_echo, many=True, atomic=False)
    refusal = await _refusal(core, spec, params={"items": "a"}, many_as_argument=True)
    assert refusal.detail == {
        "items": {"non_field_errors": ['Expected a list of items but got type "str".']}
    }


@_CORES
@pytest.mark.parametrize("flag", [True, False])
async def test_an_explicit_bundle_binding_is_accepted(core: str, flag: bool) -> None:
    """``BUNDLE`` is what a list payload does -- the whole list as one ``data`` -- so
    naming it is not a contradiction. A transport whose own default is ``BUNDLE`` would
    otherwise have to special-case every ``many=True`` spec."""
    params: Any = {"items": [_GOOD]} if flag else [_GOOD]
    result = await _dispatch(
        core,
        _spec(),
        user=None,
        params=params,
        many_as_argument=flag,
        argument_binding=ArgumentBinding.BUNDLE,
    )
    assert _plain(result.value) == [_GOOD]


@_CORES
@pytest.mark.parametrize(
    "binding", [ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS]
)
async def test_a_spread_binding_is_still_refused(core: str, binding: ArgumentBinding) -> None:
    with pytest.raises(ValueError, match="many=True"):
        await _dispatch(
            core,
            _spec(),
            user=None,
            params={"items": [_GOOD]},
            many_as_argument=True,
            argument_binding=binding,
        )


# --- errors: one shape on every DRF, keyed under the argument ----------------


@_CORES
async def test_item_errors_are_keyed_by_the_invalid_items_index(core: str) -> None:
    """DRF below 3.18 reports a list with an empty entry per valid item, and 3.18 a
    mapping of the invalid items' indexes. Both reach a client as the latter."""
    refusal = await _refusal(
        core, _spec(), params={"items": [_GOOD, _BAD, _GOOD, _BAD]}, many_as_argument=True
    )
    assert refusal.detail == {"items": {1: _BAD_DETAIL, 3: _BAD_DETAIL}}
    detail: Any = refusal.detail
    assert all(isinstance(index, int) for index in detail["items"])


@_CORES
async def test_item_errors_reported_as_a_list_are_keyed_by_index_too(core: str) -> None:
    refusal = await _refusal(
        core,
        _spec(_LineReportedAsAList),
        params={"items": [_GOOD, _BAD, _GOOD, _BAD]},
        many_as_argument=True,
    )
    assert refusal.detail == {"items": {1: _BAD_DETAIL, 3: _BAD_DETAIL}}
    assert refusal.get_codes() == {
        "items": {
            1: {"sku": ["blank"], "quantity": ["invalid"]},
            3: {"sku": ["blank"], "quantity": ["invalid"]},
        }
    }


@_CORES
async def test_without_the_flag_item_errors_keep_the_shape_drf_gave(core: str) -> None:
    refusal = await _refusal(core, _spec(_LineReportedAsAList), params=[_GOOD, _BAD])
    assert refusal.detail == [{}, _BAD_DETAIL]


@_CORES
async def test_every_item_invalid_is_keyed_from_zero(core: str) -> None:
    refusal = await _refusal(core, _spec(), params={"items": [_BAD, _BAD]}, many_as_argument=True)
    assert refusal.detail == {"items": {0: _BAD_DETAIL, 1: _BAD_DETAIL}}


@_CORES
async def test_item_errors_keep_their_codes(core: str) -> None:
    refusal = await _refusal(core, _spec(), params={"items": [_GOOD, _BAD]}, many_as_argument=True)
    assert refusal.get_codes() == {"items": {1: {"sku": ["blank"], "quantity": ["invalid"]}}}


@_CORES
@pytest.mark.parametrize(("value", "type_name"), [({"sku": "a"}, "dict"), ("a", "str"), (3, "int")])
async def test_a_non_list_is_refused_under_the_argument(
    core: str, value: Any, type_name: str
) -> None:
    refusal = await _refusal(core, _spec(), params={"items": value}, many_as_argument=True)
    assert refusal.detail == {
        "items": {"non_field_errors": [f'Expected a list of items but got type "{type_name}".']}
    }
    assert refusal.get_codes() == {"items": {"non_field_errors": ["not_a_list"]}}


@_CORES
async def test_a_null_list_is_refused_as_a_null_field(core: str) -> None:
    refusal = await _refusal(core, _spec(), params={"items": None}, many_as_argument=True)
    assert refusal.detail == {"items": ["This field may not be null."]}
    assert refusal.get_codes() == {"items": ["null"]}


@_CORES
async def test_a_missing_list_is_refused_as_a_required_field(core: str) -> None:
    refusal = await _refusal(core, _spec(), params={}, many_as_argument=True)
    assert refusal.detail == {"items": ["This field is required."]}
    assert refusal.get_codes() == {"items": ["required"]}


@_CORES
async def test_the_declared_argument_names_the_refusal(core: str) -> None:
    refusal = await _refusal(
        core, _spec(many_argument="lines"), params={"items": [_GOOD]}, many_as_argument=True
    )
    assert refusal.detail == {"lines": ["This field is required."]}


@_CORES
async def test_an_empty_list_that_may_not_be_empty_is_refused_under_the_argument(
    core: str,
) -> None:
    refusal = await _refusal(
        core, _spec(_NonEmptyLine), params={"items": []}, many_as_argument=True
    )
    assert refusal.detail == {"items": {"non_field_errors": ["This list may not be empty."]}}


@_CORES
@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([_GOOD], "Ensure this field has at least 2 elements."),
        ([_GOOD] * 4, "Ensure this field has no more than 3 elements."),
    ],
    ids=["too-short", "too-long"],
)
async def test_a_length_bound_is_refused_under_the_argument(
    core: str, items: list[Any], message: str
) -> None:
    refusal = await _refusal(
        core, _spec(_BoundedLine), params={"items": items}, many_as_argument=True
    )
    assert refusal.detail == {"items": {"non_field_errors": [message]}}


@_CORES
@pytest.mark.parametrize("policy", list(UnknownArguments))
async def test_an_argument_beside_the_list_is_refused_whatever_the_policy(
    core: str, policy: UnknownArguments
) -> None:
    """Nothing beside the list has anywhere to go: the service receives the list as its
    one ``data``. ``unknown_arguments`` still governs the keys inside each item."""
    refusal = await _refusal(
        core,
        _spec(),
        params={"items": [_GOOD], "note": 1, "extra": 2},
        many_as_argument=True,
        unknown_arguments=policy,
    )
    assert refusal.detail == {"non_field_errors": ["Unexpected argument(s): 'extra', 'note'."]}


@_CORES
async def test_a_reserved_seed_beside_the_list_is_not_an_unexpected_argument(core: str) -> None:
    """The exemption ``UnknownArguments.REJECT`` makes, so the two refusals agree."""
    result = await _dispatch(
        core,
        _spec(),
        user=None,
        params={"items": [_GOOD], "user": "someone"},
        many_as_argument=True,
    )
    assert _plain(result.value) == [_GOOD]


@_CORES
async def test_item_errors_answer_before_an_unexpected_argument(core: str) -> None:
    """The workaround validates before it looks for unknown arguments."""
    refusal = await _refusal(
        core, _spec(), params={"items": [_BAD], "note": 1}, many_as_argument=True
    )
    assert refusal.detail == {"items": {0: _BAD_DETAIL}}


@_CORES
async def test_an_undeclared_key_inside_an_item_is_refused_at_its_index(core: str) -> None:
    refusal = await _refusal(
        core,
        _spec(),
        params={"items": [_GOOD, {**_GOOD, "bogus": 1}]},
        many_as_argument=True,
        unknown_arguments=UnknownArguments.REJECT,
    )
    assert refusal.detail == {
        "items": {1: {"non_field_errors": ["Unexpected argument(s): 'bogus'."]}}
    }


@_CORES
async def test_without_the_flag_an_undeclared_key_inside_an_item_is_refused_unindexed(
    core: str,
) -> None:
    refusal = await _refusal(
        core,
        _spec(),
        params=[_GOOD, {**_GOOD, "bogus": 1}],
        unknown_arguments=UnknownArguments.REJECT,
    )
    assert refusal.detail == {"non_field_errors": ["Unexpected argument(s): 'bogus'."]}


@_CORES
async def test_arguments_that_are_not_an_object_are_refused(core: str) -> None:
    refusal = await _refusal(core, _spec(), params=[_GOOD], many_as_argument=True)
    assert refusal.detail == {
        "non_field_errors": ["Invalid data. Expected a dictionary, but got list."]
    }
    assert refusal.get_codes() == {"non_field_errors": ["invalid"]}


@_CORES
async def test_a_service_refusal_is_passed_on_as_the_service_raised_it(core: str) -> None:
    """Only the kernel's own validation is keyed. A precondition or service speaks about
    the list it received, which carries no argument name to key under."""

    def refuse() -> None:
        raise ServiceValidationError({"sku": ["Duplicate SKU."]})

    with pytest.raises(ServiceValidationError) as caught:
        await _dispatch(
            core,
            _spec(preconditions=[refuse]),
            user=None,
            params={"items": [_GOOD]},
            many_as_argument=True,
        )
    assert caught.value.detail == {"sku": ["Duplicate SKU."]}


# --- the workaround, measured ------------------------------------------------
#
# Every case here produces the same detail and codes on every supported DRF from
# the workaround, so the comparison is exact rather than version-dependent. Item
# errors are the one shape that is not, and are pinned literally above.

_WORKAROUND_CASES: list[tuple[str, type, type, Callable[[], Any]]] = [
    ("missing", _Lines, _Line, lambda: {}),
    ("null", _Lines, _Line, lambda: {"items": None}),
    ("object", _Lines, _Line, lambda: {"items": {"sku": "a"}}),
    ("string", _Lines, _Line, lambda: {"items": "a"}),
    ("unexpected-argument", _Lines, _Line, lambda: {"items": [_GOOD], "note": 1}),
    ("missing-and-unexpected", _Lines, _Line, lambda: {"note": 1}),
    ("empty", _NonEmptyLinesInput, _NonEmptyLine, lambda: {"items": []}),
]


@_CORES
@pytest.mark.parametrize(
    ("wrapper", "item", "make_params"),
    [(wrapper, item, make) for _, wrapper, item, make in _WORKAROUND_CASES],
    ids=[name for name, _, _, _ in _WORKAROUND_CASES],
)
async def test_the_refusal_is_the_workarounds(
    core: str, wrapper: type, item: type, make_params: Callable[[], Any]
) -> None:
    workaround = ServiceSpec(service=_echo, input_serializer=wrapper, atomic=False)
    expected = await _refusal(
        core, workaround, params=make_params(), unknown_arguments=UnknownArguments.REJECT
    )
    refusal = await _refusal(
        core,
        _spec(item),
        params=make_params(),
        many_as_argument=True,
        unknown_arguments=UnknownArguments.REJECT,
    )
    assert refusal.detail == expected.detail
    assert refusal.get_codes() == expected.get_codes()
