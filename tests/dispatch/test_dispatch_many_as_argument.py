"""``many_as_argument`` -- a ``many=True`` list read out of an object of named arguments.

A caller whose input is always a JSON object cannot send the bare array a
``many=True`` spec validates over HTTP. With ``many_as_argument=True`` the list
arrives under ``spec.many_argument`` instead, and every refusal is keyed under that
argument as the kernel addresses rows: a row's by its ``int`` index, only failing
rows present, and a refusal of the list as a whole as a list under the argument.
Each row is shape-checked before the input serializer reads it, so a value of the
wrong type is refused in the shape check's words and a blank one in DRF's.

Every case runs through both cores, because the flag is threaded through each.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from rest_framework_services import (
    ArgumentBinding,
    SelectorKind,
    SelectorSpec,
    ServiceCreateView,
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
# Well-typed, so the shape check passes it and the input serializer refuses it.
_BAD: dict[str, Any] = {"sku": "", "quantity": 1}
_BAD_DETAIL: dict[str, Any] = {"sku": ["This field may not be blank."]}
# Mistyped, so the shape check refuses it before any serializer reads it.
_MISTYPED: dict[str, Any] = {"sku": "a", "quantity": "x"}


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
    assert refusal.get_codes() == {"non_field_errors": ["not_a_list"]}


@_CORES
@pytest.mark.parametrize("params", ["a", 3], ids=["str", "int"])
async def test_without_the_flag_arguments_that_are_no_list_are_refused_in_drfs_words(
    core: str, params: Any
) -> None:
    """As a list serializer refuses them, naming what arrived, as 0.57.0 did."""
    refusal = await _refusal(core, _spec(), params=params)
    name = type(params).__name__
    assert refusal.detail == {
        "non_field_errors": [f'Expected a list of items but got type "{name}".']
    }


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
async def test_without_an_input_serializer_a_list_is_refused(core: str) -> None:
    """The input serializer is what declares a row, so a ``many`` spec with none has
    no rows to read."""
    spec = ServiceSpec(service=_echo, many=True, atomic=False)
    with pytest.raises(ImproperlyConfigured, match="many=True with no validator"):
        await _dispatch(core, spec, user=None, params={"items": []}, many_as_argument=True)


@_CORES
@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.AUTO, ArgumentBinding.BUNDLE, ArgumentBinding.SPREAD_AUTHOR_WINS],
)
async def test_an_explicit_binding_is_accepted(
    core: str, flag: bool, binding: ArgumentBinding
) -> None:
    """Each binding means the one argument pool, and a list payload is the whole list
    as one ``data``, so naming one is not a contradiction. A transport whose own
    default is ``BUNDLE`` would otherwise have to special-case every ``many=True``
    spec. Every binding hands the service the same list, as ``AUTO`` does."""
    params: Any = {"items": [_GOOD]} if flag else [_GOOD]
    result = await _dispatch(
        core,
        _spec(),
        user=None,
        params=params,
        many_as_argument=flag,
        argument_binding=binding,
    )
    assert _plain(result.value) == [_GOOD]


# --- errors: keyed under the argument, rows by index ---------------------------


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
    assert refusal.get_codes() == {"items": {1: {"sku": ["blank"]}, 3: {"sku": ["blank"]}}}


def _live_view() -> ServiceCreateView:
    view = ServiceCreateView()
    view.request = Request(APIRequestFactory().post("/", {}, format="json"), parsers=[JSONParser()])
    view.request.user = AnonymousUser()
    view.kwargs = {}
    view.format_kwarg = None
    return view


_OVER_A_LIVE_VIEW = {
    "a-list-of-errors": (_LineReportedAsAList, [_GOOD, _BAD, _GOOD, _BAD], UnknownArguments.IGNORE),
    "an-undeclared-key": (_Line, [_GOOD, {**_GOOD, "bogus": 1}], UnknownArguments.REJECT),
}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("serializer", "rows", "policy"), _OVER_A_LIVE_VIEW.values(), ids=_OVER_A_LIVE_VIEW.keys()
)
def test_over_a_live_view_rows_under_an_argument_are_refused_as_off_http(
    serializer: type, rows: list[Any], policy: UnknownArguments
) -> None:
    """Rows under an argument are no body the view's list serializer answers whole,
    so a live view refuses them by index, each row once, as a call off HTTP does."""
    view = _live_view()
    with pytest.raises(ValidationError) as caught:
        dispatch_spec(
            _spec(serializer),
            user=None,
            params={"items": rows},
            request=view.request,
            view=view,
            many_as_argument=True,
            unknown_arguments=policy,
        )
    refusal = caught.value.detail
    expected = (
        {1: _BAD_DETAIL, 3: _BAD_DETAIL}
        if serializer is _LineReportedAsAList
        else {1: {"non_field_errors": ["Unexpected argument(s): 'bogus'."]}}
    )
    assert refusal == {"items": expected}


class _CountedLine(_Line):
    """Records each row it is handed to read."""

    read: ClassVar[list[Any]] = []

    def to_internal_value(self, data: Any) -> Any:
        _CountedLine.read.append(data)
        return super().to_internal_value(data)


def test_over_a_live_view_a_row_the_kernel_refused_is_not_read_again() -> None:
    """Rows under an argument are bound as off HTTP: the serializer reads the rows
    the kernel passed, and the one it refused for being no object is answered
    without the serializer reading it a second time."""
    _CountedLine.read.clear()
    view = _live_view()

    with pytest.raises(ValidationError) as caught:
        dispatch_spec(
            _spec(_CountedLine),
            user=None,
            params={"items": [1, _BAD]},
            request=view.request,
            view=view,
            many_as_argument=True,
        )

    assert caught.value.detail == {
        "items": {
            0: {"non_field_errors": ["Invalid data. Expected a dictionary, but got int."]},
            1: _BAD_DETAIL,
        }
    }
    assert _CountedLine.read == [_BAD]


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
    assert refusal.get_codes() == {"items": {1: {"sku": ["blank"]}}}


@_CORES
async def test_a_mistyped_item_is_refused_by_the_shape_check_at_its_index(core: str) -> None:
    refusal = await _refusal(
        core, _spec(), params={"items": [_GOOD, _MISTYPED]}, many_as_argument=True
    )
    assert refusal.detail == {"items": {1: {"quantity": ["Expected an integer."]}}}
    assert refusal.get_codes() == {"items": {1: {"quantity": ["invalid"]}}}


@_CORES
@pytest.mark.parametrize("value", [{"sku": "a"}, "a", 3], ids=["dict", "str", "int"])
async def test_a_non_list_is_refused_under_the_argument(core: str, value: Any) -> None:
    refusal = await _refusal(core, _spec(), params={"items": value}, many_as_argument=True)
    assert refusal.detail == {"items": ["Expected an array."]}
    assert refusal.get_codes() == {"items": ["not_a_list"]}


@_CORES
async def test_a_null_list_is_refused_as_a_null_field(core: str) -> None:
    refusal = await _refusal(core, _spec(), params={"items": None}, many_as_argument=True)
    assert refusal.detail == {"items": ["This field cannot be null."]}
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
    assert refusal.detail == {"items": ["Ensure this field has at least 1 elements."]}
    assert refusal.get_codes() == {"items": ["min_length"]}


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
    assert refusal.detail == {"items": [message]}


@_CORES
@pytest.mark.parametrize(
    "policy", [UnknownArguments.IGNORE, UnknownArguments.REJECT], ids=["ignore", "reject"]
)
@pytest.mark.parametrize(
    ("beside", "named"),
    [({"note": 1, "extra": 2}, "'extra', 'note'"), ({"view": 1}, "'view'")],
    ids=["undeclared", "transport-name"],
)
async def test_an_argument_beside_the_list_is_refused_under_every_policy(
    core: str, policy: UnknownArguments, beside: dict[str, Any], named: str
) -> None:
    """Nothing beside the list has anywhere to go: the service is handed the list
    alone, so ``IGNORE`` would drop the argument without a trace. Refused, as
    0.57.0 refused it, once the rows are valid, and the service never runs."""
    ran: list[Any] = []

    def _record(*, data: Any) -> Any:
        ran.append(data)
        return data

    refusal = await _refusal(
        core,
        ServiceSpec(service=_record, input_serializer=_Line, many=True, atomic=False),
        params={"items": [_GOOD], **beside},
        many_as_argument=True,
        unknown_arguments=policy,
    )
    assert refusal.detail == {"non_field_errors": [f"Unexpected argument(s): {named}."]}
    assert ran == []


@_CORES
async def test_an_argument_beside_the_list_is_unknown_under_reject(core: str) -> None:
    refusal = await _refusal(
        core,
        _spec(),
        params={"items": [_GOOD], "note": 1, "extra": 2},
        many_as_argument=True,
        unknown_arguments=UnknownArguments.REJECT,
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
async def test_item_errors_answer_before_an_argument_beside_the_list(core: str) -> None:
    """Under the default ``IGNORE`` the rows are validated first, as on 0.57.0, so
    the rows' refusal is the whole answer."""
    refusal = await _refusal(
        core, _spec(), params={"items": [_BAD], "note": 1}, many_as_argument=True
    )
    assert refusal.detail == {"items": {0: _BAD_DETAIL}}


@_CORES
async def test_a_row_the_kernel_refuses_answers_before_an_argument_beside_the_list(
    core: str,
) -> None:
    """A row missing a field is refused by the kernel's own check, before the
    serializer sees the rows, and that refusal is the answer, as on 0.57.0."""
    refusal = await _refusal(
        core, _spec(), params={"items": [_GOOD, {}], "note": 1}, many_as_argument=True
    )
    assert "items" in refusal.detail
    assert "non_field_errors" not in refusal.detail


@_CORES
async def test_under_reject_an_argument_beside_the_list_is_refused_before_the_items(
    core: str,
) -> None:
    refusal = await _refusal(
        core,
        _spec(),
        params={"items": [_BAD], "note": 1},
        many_as_argument=True,
        unknown_arguments=UnknownArguments.REJECT,
    )
    assert refusal.detail == {"non_field_errors": ["Unexpected argument(s): 'note'."]}


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
async def test_without_the_flag_an_undeclared_key_inside_an_item_is_refused_in_the_list_shape(
    core: str,
) -> None:
    refusal = await _refusal(
        core,
        _spec(),
        params=[_GOOD, {**_GOOD, "bogus": 1}],
        unknown_arguments=UnknownArguments.REJECT,
    )
    assert refusal.detail == [{}, {"non_field_errors": ["Unexpected argument(s): 'bogus'."]}]


@_CORES
async def test_arguments_that_are_not_an_object_are_refused(core: str) -> None:
    refusal = await _refusal(core, _spec(), params=[_GOOD], many_as_argument=True)
    assert refusal.detail == {
        "non_field_errors": ["Invalid data. Expected a dictionary, but got list."]
    }
    assert refusal.get_codes() == {"non_field_errors": ["invalid"]}


_NOT_AN_OBJECT = {
    "int": (3, {"non_field_errors": ["Invalid data. Expected a dictionary, but got int."]}),
    "list": ([1], {"non_field_errors": ["Invalid data. Expected a dictionary, but got list."]}),
    "str": ("a", {"non_field_errors": ["Invalid data. Expected a dictionary, but got str."]}),
    "null": (None, ["This field may not be null."]),
}


@_CORES
@pytest.mark.parametrize(("row", "expected"), _NOT_AN_OBJECT.values(), ids=_NOT_AN_OBJECT.keys())
async def test_a_row_that_is_no_object_is_refused_at_its_index_in_drfs_words(
    core: str, row: Any, expected: Any
) -> None:
    """As the list serializer refuses such a row, as 0.57.0 did, beside the
    other rows' own refusals."""
    refusal = await _refusal(core, _spec(), params={"items": [_BAD, row]}, many_as_argument=True)
    assert refusal.detail == {"items": {0: _BAD_DETAIL, 1: expected}}


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
