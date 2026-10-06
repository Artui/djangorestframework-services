"""Tests for the shared ``types`` helpers."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any, ForwardRef

import pytest
from django.core.exceptions import ImproperlyConfigured
from typing_extensions import Format, evaluate_forward_ref

from rest_framework_services import RelationOrphan
from rest_framework_services.types.input_required import InputRequired
from rest_framework_services.types.marked_input_keys import marked_input_keys
from rest_framework_services.types.not_client_input import NotClientInput
from rest_framework_services.types.utils import (
    callable_type_hints,
    pk_input_targets,
    validate_metadata,
    validate_pk_field_map,
    validate_relation_orphan,
)
from tests.testapp.models import Author, Profile

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


class TestValidateRelationOrphan:
    def test_every_member_is_accepted_as_a_member_or_a_string(self) -> None:
        for orphan in RelationOrphan:
            validate_relation_orphan(orphan, delete_service=None, label="ChildSpec")
            validate_relation_orphan(orphan.value, delete_service=None, label="ChildSpec")

    def test_an_unknown_value_raises(self) -> None:
        with pytest.raises(ValueError, match="ChildSpec.orphan must be one of"):
            validate_relation_orphan("archive", delete_service=None, label="ChildSpec")

    def test_the_default_composes_with_a_delete_service(self) -> None:
        # Every spec written before the field existed says ``AUTO``, so it is
        # not a second answer to the question the service already answers.
        validate_relation_orphan(RelationOrphan.AUTO, delete_service=_service, label="ChildSpec")

    def test_stating_it_beside_a_delete_service_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured) as excinfo:
            validate_relation_orphan("unlink", delete_service=_service, label="ChildSpec")
        message = str(excinfo.value)
        assert "ChildSpec: orphan='unlink' declared alongside delete_service" in message
        assert "would decide nothing" in message
        # The remedy names both directions.
        assert "Dispose of the row in the service" in message
        assert "drop the service" in message


class TestPkInputTargets:
    def test_every_spelling_of_an_implicit_key(self) -> None:
        assert pk_input_targets(Author) == frozenset({"pk", "id"})

    def test_the_name_and_attname_collapse_for_an_implicit_key(self) -> None:
        # ``name`` and ``attname`` are both ``id`` here; they diverge only where
        # a relation *is* the key, which is why both are read rather than one.
        assert pk_input_targets(Profile) == frozenset({"pk", "id"})


class TestValidatePkFieldMap:
    def test_no_field_map_is_allowed(self) -> None:
        validate_pk_field_map(label="ChildSpec", model=Author, match_key="pk", field_map=None)

    def test_a_field_map_leaving_the_key_alone_is_allowed(self) -> None:
        validate_pk_field_map(
            label="ChildSpec", model=Author, match_key="pk", field_map={"full_name": "name"}
        )

    def test_renaming_onto_the_key_is_refused_when_the_match_is_the_key(self) -> None:
        with pytest.raises(ImproperlyConfigured) as excinfo:
            validate_pk_field_map(
                label="ChildSpec", model=Author, match_key="pk", field_map={"ident": "pk"}
            )
        message = str(excinfo.value)
        assert "ChildSpec: field_map renames 'ident' onto the primary key of Author" in message
        assert "match_key='pk'" in message
        # The remedy names both directions.
        assert "Send the key under a name match_key reads" in message
        assert "match on a field the mapping does not rename" in message

    def test_every_spelling_of_the_key_is_seen_on_both_sides(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="renames 'ident'"):
            validate_pk_field_map(
                label="ChildSpec", model=Author, match_key="id", field_map={"ident": "id"}
            )

    def test_a_natural_match_key_makes_the_same_mapping_coherent(self) -> None:
        # Nothing is unreachable here: the row matches on its natural key and
        # the alias goes on guarding creates, exactly as a plain ``pk`` would.
        validate_pk_field_map(
            label="ChildSpec", model=Author, match_key="name", field_map={"ident": "pk"}
        )


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
