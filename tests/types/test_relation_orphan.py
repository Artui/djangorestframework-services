"""``RelationOrphan`` — the enum, and which specs carry it."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services import (
    ChildSpec,
    ForwardRelationSpec,
    GenericRelationSpec,
    ManyToManySpec,
    RelationOrphan,
    ReverseOneToOneSpec,
)
from tests.testapp.models import Attachment, Author, Profile, Section, Tag


def _service(**_: Any) -> None: ...


# One builder per kind whose rows the parent owns, so which can have an orphan.
_OWNING_KINDS: dict[str, Callable[..., Any]] = {
    "ChildSpec": lambda **kw: ChildSpec(model=Section, fk="catalog", **kw),
    "GenericRelationSpec": lambda **kw: GenericRelationSpec(model=Attachment, **kw),
    "ReverseOneToOneSpec": lambda **kw: ReverseOneToOneSpec(model=Profile, fk="author", **kw),
}


class TestTheEnum:
    def test_the_members_spell_their_values(self) -> None:
        assert [orphan.value for orphan in RelationOrphan] == ["auto", "unlink", "delete"]

    def test_a_member_compares_equal_to_the_plain_string(self) -> None:
        # As ``RelationMode`` and ``RelationOutcome`` do, so a caller may spell
        # it either way and a serialized value stays readable.
        assert RelationOrphan.DELETE == "delete"


class TestOnlyTheKindsThatOwnTheirRowsCarryIt:
    def test_every_owning_kind_defaults_to_auto(self) -> None:
        assert [build().orphan for build in _OWNING_KINDS.values()] == [RelationOrphan.AUTO] * 3

    def test_every_owning_kind_checks_the_value(self) -> None:
        for label, build in _OWNING_KINDS.items():
            with pytest.raises(ValueError, match=f"{label}.orphan must be one of"):
                build(orphan="archive")

    def test_every_owning_kind_refuses_it_beside_a_delete_service(self) -> None:
        for label, build in _OWNING_KINDS.items():
            with pytest.raises(ImproperlyConfigured, match=f"{label}: orphan="):
                build(orphan="delete", delete_service=_service)

    def test_the_kinds_that_own_nothing_have_no_flag(self) -> None:
        # A many-to-many target is shared and never deleted, and a forward
        # relation removes nothing at all — neither has an orphan to dispose of.
        assert not hasattr(ManyToManySpec(model=Tag), "orphan")
        assert not hasattr(ForwardRelationSpec(model=Author), "orphan")
