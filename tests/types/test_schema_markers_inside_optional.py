"""A schema marker written inside an author-spelled ``X | None`` applies, or is refused.

``Annotated[int, NotClientInput] | None`` and ``Annotated[int | None, NotClientInput]``
declare the same thing, so the placement of ``| None`` must not decide whether a key
is hidden, required or described. One level of ``Optional`` is read through, on an
ordinary parameter and on an ``Unpack[TypedDict]`` key alike; anything wider is
refused rather than guessed at.

Every test here drives the public surface (``spec_to_json_schema``, ``dispatch_spec``
and the two readers' existing signatures), so each fails rather than errors against a
tree without the fix.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Optional, Union, get_args

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import ValidationError
from typing_extensions import TypedDict, Unpack

from rest_framework_services import (
    InputDescription,
    InputRequired,
    NotClientInput,
    SelectorKind,
    SelectorSpec,
    UnknownArguments,
    dispatch_spec,
    spec_to_json_schema,
)
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.read_input_description import read_input_description
from rest_framework_services.types.read_schema_markers import read_schema_markers

_NULLABLE_INTEGER = {"anyOf": [{"type": "integer"}, {"type": "null"}]}


# --------------------------------------------------------------- the selectors
# One per (marker, spelling, surface). The pipe and ``Optional`` spellings are both
# covered because they are different objects at runtime (``types.UnionType`` and
# ``typing.Union``), and a reader that recognised one would still miss the other.


def _hidden_pipe(*, team: Annotated[int, NotClientInput] | None = None) -> Any:
    return team


def _hidden_optional(*, team: Optional[Annotated[int, NotClientInput]] = None) -> Any:  # noqa: UP045 -- the spelling under test
    return team


def _required_pipe(*, limit: Annotated[int, InputRequired] | None = None) -> Any:
    return limit


def _required_optional(*, limit: Optional[Annotated[int, InputRequired]] = None) -> Any:  # noqa: UP045
    return limit


def _described_pipe(*, limit: Annotated[int, InputDescription("Page size")] | None = None) -> Any:
    return limit


def _described_optional(
    *,
    limit: Optional[Annotated[int, InputDescription("Page size")]] = None,  # noqa: UP045
) -> Any:
    return limit


class _HiddenPipe(TypedDict, total=False):
    team: Annotated[int, NotClientInput] | None


class _HiddenOptional(TypedDict, total=False):
    team: Optional[Annotated[int, NotClientInput]]  # noqa: UP045


class _RequiredPipe(TypedDict, total=False):
    limit: Annotated[int, InputRequired] | None


class _RequiredOptional(TypedDict, total=False):
    limit: Optional[Annotated[int, InputRequired]]  # noqa: UP045


class _DescribedPipe(TypedDict, total=False):
    limit: Annotated[int, InputDescription("Page size")] | None


class _DescribedOptional(TypedDict, total=False):
    limit: Optional[Annotated[int, InputDescription("Page size")]]  # noqa: UP045


def _hidden_pipe_key(**extras: Unpack[_HiddenPipe]) -> Any:
    return extras


def _hidden_optional_key(**extras: Unpack[_HiddenOptional]) -> Any:
    return extras


def _required_pipe_key(**extras: Unpack[_RequiredPipe]) -> Any:
    return extras


def _required_optional_key(**extras: Unpack[_RequiredOptional]) -> Any:
    return extras


def _described_pipe_key(**extras: Unpack[_DescribedPipe]) -> Any:
    return extras


def _described_optional_key(**extras: Unpack[_DescribedOptional]) -> Any:
    return extras


_SURFACES = ["parameter-pipe", "parameter-optional", "typed-dict-pipe", "typed-dict-optional"]


def _input_schema(selector: Any) -> dict[str, Any]:
    return spec_to_json_schema(SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector))


# --------------------------------------------------------------- NotClientInput


@pytest.mark.parametrize(
    "selector",
    [_hidden_pipe, _hidden_optional, _hidden_pipe_key, _hidden_optional_key],
    ids=_SURFACES,
)
class TestNotClientInputInsideOptional:
    def test_the_key_is_not_advertised(self, selector: Any) -> None:
        assert "team" not in _input_schema(selector).get("properties", {})

    def test_a_caller_value_is_refused_under_reject(self, selector: Any) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector)
        with pytest.raises(ValidationError) as exc:
            dispatch_spec(
                spec, user=None, params={"team": 7}, unknown_arguments=UnknownArguments.REJECT
            )
        assert exc.value.detail == {"non_field_errors": ["Unexpected argument(s): 'team'."]}


# ---------------------------------------------------------------- InputRequired


@pytest.mark.parametrize(
    "selector",
    [_required_pipe, _required_optional, _required_pipe_key, _required_optional_key],
    ids=_SURFACES,
)
class TestInputRequiredInsideOptional:
    def test_the_key_is_required_and_keeps_its_null_branch(self, selector: Any) -> None:
        schema = _input_schema(selector)
        assert schema.get("required") == ["limit"]
        assert schema["properties"]["limit"] == _NULLABLE_INTEGER

    def test_dispatch_refuses_a_call_without_it(self, selector: Any) -> None:
        spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector)
        with pytest.raises(ServiceValidationError) as exc:
            dispatch_spec(spec, user=None, params={})
        assert exc.value.detail == {"non_field_errors": ["Missing required argument(s): 'limit'."]}


# ------------------------------------------------------------- InputDescription


@pytest.mark.parametrize(
    "selector",
    [_described_pipe, _described_optional, _described_pipe_key, _described_optional_key],
    ids=_SURFACES,
)
def test_a_description_inside_optional_is_published(selector: Any) -> None:
    assert _input_schema(selector)["properties"]["limit"] == {
        **_NULLABLE_INTEGER,
        "description": "Page size",
    }


# --------------------------------------------------------------- the readers


class TestReadThroughOneOptional:
    @pytest.mark.parametrize(
        "annotation",
        [
            Annotated[int, NotClientInput] | None,
            Optional[Annotated[int, NotClientInput]],  # noqa: UP045
            Union[Annotated[int, NotClientInput], None],  # noqa: UP007
        ],
        ids=["pipe", "optional", "union"],
    )
    def test_the_marker_is_read_and_the_null_branch_kept(self, annotation: Any) -> None:
        underlying, required, hidden = read_schema_markers(annotation)
        assert (required, hidden) == (False, True)
        assert get_args(underlying) == (int, type(None))

    def test_the_author_s_member_order_is_kept(self) -> None:
        underlying, _required, hidden = read_schema_markers(None | Annotated[int, NotClientInput])
        assert hidden is True
        assert get_args(underlying) == (type(None), int)

    def test_an_optional_inside_the_marker_is_not_doubled(self) -> None:
        underlying, required, _hidden = read_schema_markers(
            Annotated[int | None, InputRequired] | None
        )
        assert required is True
        assert get_args(underlying) == (int, type(None))

    def test_a_plain_optional_is_returned_as_written(self) -> None:
        # Nothing to read through: no ``Annotated`` member, so no layer.
        annotation = int | None
        assert read_schema_markers(annotation) == (annotation, False, False)
        assert read_input_description(annotation) is None

    def test_the_description_reader_reads_through_it_too(self) -> None:
        assert read_input_description(Annotated[int, InputDescription("Size")] | None) == "Size"

    def test_the_contradiction_is_refused_inside_optional(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="both InputRequired and NotClientInput"):
            read_schema_markers(Annotated[int, InputRequired, NotClientInput] | None)

    def test_a_description_beside_not_client_input_is_refused_inside_optional(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="both described and NotClientInput"):
            read_input_description(Annotated[int, InputDescription("Size"), NotClientInput] | None)

    @pytest.mark.parametrize(
        "annotation",
        [
            Annotated[int, "help text"] | None,
            Annotated[int, "help text"] | str,
            list[Annotated[int, "help text"]],
            int | str | None,
        ],
        ids=["foreign-in-optional", "foreign-in-union", "foreign-nested", "plain-union"],
    )
    def test_foreign_metadata_anywhere_is_not_a_marker(self, annotation: Any) -> None:
        # ``Annotated`` is a shared channel; only the package's own markers are
        # placed, so another library's metadata stays legal at any depth.
        _underlying, required, hidden = read_schema_markers(annotation)
        assert (required, hidden) == (False, False)
        assert read_input_description(annotation) is None


# ---------------------------------------------------------- what is refused


_WIDER = [
    pytest.param(
        Annotated[int, NotClientInput] | Annotated[str, InputRequired], id="two-marked-members"
    ),
    pytest.param(Annotated[int, NotClientInput] | str, id="marked-member-beside-a-type"),
    pytest.param(
        Annotated[int, NotClientInput] | str | None, id="marked-member-in-a-wide-optional"
    ),
    pytest.param(list[Annotated[int, NotClientInput]], id="nested-in-a-container"),
    pytest.param(dict[str, Annotated[int, InputRequired]], id="nested-in-a-mapping-value"),
    pytest.param(
        Annotated[list[Annotated[int, NotClientInput]], "help text"], id="nested-under-annotated"
    ),
    pytest.param(
        Annotated[Annotated[int, NotClientInput] | None, "help text"],
        id="optional-under-annotated",
    ),
    pytest.param(
        Annotated[list[Annotated[int, NotClientInput]], "help text"] | None,
        id="nested-inside-the-optional-member",
    ),
    pytest.param(list[Annotated[int, InputDescription("Size")]], id="nested-description"),
    pytest.param(
        list[Annotated[list[Annotated[int, NotClientInput]], "help text"]],
        id="nested-under-foreign-annotated",
    ),
    pytest.param(
        Callable[[Annotated[int, NotClientInput]], int], id="nested-in-callable-parameters"
    ),
]


class TestWiderPlacementIsRefused:
    @pytest.mark.parametrize("annotation", _WIDER)
    def test_read_schema_markers_refuses(self, annotation: Any) -> None:
        with pytest.raises(ImproperlyConfigured, match="schema marker") as exc:
            read_schema_markers(annotation)
        # The refusal names the spelling that works, so the fix is one edit.
        assert "Annotated[X | None, Marker]" in str(exc.value)

    @pytest.mark.parametrize("annotation", _WIDER)
    def test_read_input_description_refuses(self, annotation: Any) -> None:
        with pytest.raises(ImproperlyConfigured, match="schema marker"):
            read_input_description(annotation)

    def test_reflection_refuses_rather_than_advertising_the_key(self) -> None:
        def selector(*, team: Annotated[int, NotClientInput] | str | None = None) -> Any:
            return team

        with pytest.raises(ImproperlyConfigured, match="schema marker"):
            _input_schema(selector)
