"""Tests for the shared ``types`` helpers."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any, ForwardRef

import pytest
from django.core.exceptions import ImproperlyConfigured
from typing_extensions import Format, evaluate_forward_ref

from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.marked_input_keys import marked_input_keys
from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.utils import (
    callable_type_hints,
    validate_metadata,
)

if TYPE_CHECKING:
    # Only a type checker sees it, so an annotation naming it does not resolve.
    from tests.testapp.models import Post as _Owner


def _service(**_: object) -> None: ...


class TestValidateMetadata:
    def test_none_is_allowed(self) -> None:
        validate_metadata(None, label="SelectorSpec")

    def test_a_dict_is_allowed(self) -> None:
        validate_metadata({"scope": "tenant"}, label="SelectorSpec")

    def test_any_mapping_is_allowed(self) -> None:
        validate_metadata(MappingProxyType({"scope": "tenant"}), label="SelectorSpec")

    def test_non_mapping_raises_with_the_label_and_the_type(self) -> None:
        with pytest.raises(ImproperlyConfigured) as excinfo:
            validate_metadata([("scope", "tenant")], label="ServiceSpec")
        message = str(excinfo.value)
        assert "ServiceSpec.metadata must be a mapping (or None); got list" in message
        assert "never reads its contents" in message


def _beside_unresolved(*, team: Annotated[str, NotClientInput], owner: _Owner) -> None: ...


class TestCallableTypeHints:
    def test_each_annotation_is_read_on_its_own(self) -> None:
        hints = callable_type_hints(_beside_unresolved)

        assert hints["team"] == Annotated[str, NotClientInput]
        assert hints["owner"] is Any

    def test_annotations_that_cannot_be_taken_read_as_none(self) -> None:
        class _Callable:
            def __call__(self, *, team: Annotated[str, NotClientInput]) -> None: ...

        broken = _Callable()
        broken.__annotations__ = "not a dict"  # type: ignore[assignment]

        assert callable_type_hints(broken) == {}

    def test_forwardref_evaluation_keeps_the_whole_text_as_one_reference(self) -> None:
        # Why ``_read_unresolved`` evaluates with stand-ins of its own: the
        # standard library's ``FORWARDREF`` evaluation leaves this spelling one
        # forward reference on every supported Python, its marker unread.
        text = "Annotated[_Owner, NotClientInput] | None"

        value = evaluate_forward_ref(ForwardRef(text), globals=globals(), format=Format.FORWARDREF)

        assert isinstance(value, ForwardRef)
        assert value.__forward_arg__ == text


_Hide = NotClientInput


@dataclass
class _Query:
    team: Annotated[str, _Hide] = "own-team"


@dataclass
class _NarrowQuery(_Query):
    limit: Annotated[_Owner | None, InputRequired] = None


def test_a_class_reads_each_bases_annotations_where_it_was_written() -> None:
    # ``_Hide`` is a marker only by this module's binding, so ``team`` is hidden
    # only when the base is read, and read here.
    assert marked_input_keys(_NarrowQuery) == (frozenset({"limit"}), frozenset({"team"}))
