"""Unit tests for ``typed_dict_input`` (PEP 563-robust required-key detection)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from typing_extensions import NotRequired, Required, TypedDict

from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.typed_dict_input import typed_dict_input

if TYPE_CHECKING:
    # Only a type checker sees it, so an annotation naming it does not resolve.
    from tests.testapp.models import Post as _Owner


class _AllOptional(TypedDict, total=False):
    a: int
    b: str


class _Mixed(TypedDict):  # total=True
    required_bare: int
    opted_out: NotRequired[str]


class _TotalFalseWithRequired(TypedDict, total=False):
    normally_optional: int
    forced: Required[str]


def test_total_false_has_no_required_keys() -> None:
    field_types, required = typed_dict_input(_AllOptional)
    assert field_types == {"a": int, "b": str}
    assert required == frozenset()


def test_not_required_demotes_under_stringized_annotations() -> None:
    # The whole point: under ``from __future__ import annotations`` the raw
    # ``__required_keys__`` misclassifies ``opted_out`` as required; the resolved
    # ``NotRequired`` wrapper corrects it, and the wrapper is stripped from the type.
    field_types, required = typed_dict_input(_Mixed)
    assert field_types == {"required_bare": int, "opted_out": str}
    assert required == frozenset({"required_bare"})


def test_required_wrapper_promotes_in_a_total_false_body() -> None:
    field_types, required = typed_dict_input(_TotalFalseWithRequired)
    assert field_types == {"normally_optional": int, "forced": str}
    assert required == frozenset({"forced"})


class _BesideUnresolved(TypedDict):
    team: Annotated[str, NotClientInput]
    owner: _Owner
    note: NotRequired[_Owner]


def test_a_key_that_does_not_resolve_costs_only_itself() -> None:
    # ``owner`` names a class only a type checker sees. Its siblings keep their
    # annotations, and ``note`` is still read as ``NotRequired``, which the
    # postponed annotation hides from ``__required_keys__``.
    field_types, required = typed_dict_input(_BesideUnresolved)

    assert field_types["team"] == Annotated[str, NotClientInput]
    assert set(field_types) == {"team", "owner", "note"}
    assert required == frozenset({"team", "owner"})
