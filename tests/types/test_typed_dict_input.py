"""Unit tests for ``typed_dict_input`` (PEP 563-robust required-key detection)."""

from __future__ import annotations

from typing_extensions import NotRequired, Required, TypedDict

from rest_framework_services.types.typed_dict_input import typed_dict_input


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
