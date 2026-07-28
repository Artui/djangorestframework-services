"""Unit tests for ``marked_input_keys``."""

from __future__ import annotations

from typing import Annotated, Any

from typing_extensions import TypedDict, Unpack

from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.marked_input_keys import marked_input_keys
from rest_framework_services.types.not_client_input import NotClientInput


class _Extras(TypedDict, total=False):
    project_pk: Annotated[int, InputRequired]
    team_role: Annotated[str, NotClientInput]
    note: str


def _with_extras(**extras: Unpack[_Extras]) -> None: ...


def _with_named(
    *,
    pk: Annotated[int, InputRequired],
    secret: Annotated[str, NotClientInput],
    plain: str,
) -> None: ...


def _unmarked(*, a: int, **kwargs: Any) -> None: ...


def _unannotated(*, a, **kwargs) -> None:  # type: ignore[no-untyped-def]
    ...


def test_reads_markers_off_an_unpacked_typed_dict() -> None:
    assert marked_input_keys(_with_extras) == (frozenset({"project_pk"}), frozenset({"team_role"}))


def test_reads_markers_off_ordinary_parameters() -> None:
    assert marked_input_keys(_with_named) == (frozenset({"pk"}), frozenset({"secret"}))


def test_unmarked_callable_returns_empty_sets() -> None:
    assert marked_input_keys(_unmarked) == (frozenset(), frozenset())


def test_unannotated_parameters_are_skipped() -> None:
    assert marked_input_keys(_unannotated) == (frozenset(), frozenset())


def test_positional_only_parameters_are_skipped() -> None:
    def positional(pk: Annotated[int, InputRequired], /) -> None: ...

    # A positional-only parameter can't be supplied from the kwargs pool, so it
    # is outside the marked surface entirely.
    assert marked_input_keys(positional) == (frozenset(), frozenset())


def test_unresolvable_annotations_degrade_to_unmarked() -> None:
    def broken(*, a: NoSuchType) -> None: ...  # type: ignore[name-defined]  # noqa: F821

    assert marked_input_keys(broken) == (frozenset(), frozenset())
