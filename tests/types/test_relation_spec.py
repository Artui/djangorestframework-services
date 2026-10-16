"""The relation declarations: what a drfs caller sees change now that they are the kernel's.

``ChildSpec``, ``ForwardRelationSpec``, ``GenericRelationSpec``,
``ManyToManySpec`` and ``ReverseOneToOneSpec`` are django-service-specs' own
classes, and their rules are tested there. Two ways of building one that drfs
accepted are refused when it is built.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from rest_framework_services.types.child_spec import ChildSpec
from rest_framework_services.types.forward_relation_spec import ForwardRelationSpec
from rest_framework_services.types.generic_relation_spec import GenericRelationSpec
from rest_framework_services.types.many_to_many_spec import ManyToManySpec
from rest_framework_services.types.reverse_one_to_one_spec import ReverseOneToOneSpec
from tests.testapp.models import Attachment, Author, Profile, Section, Tag

# Each kind built with its required arguments by keyword, plus ``extra``.
_KINDS: dict[str, Callable[..., Any]] = {
    "ChildSpec": lambda **extra: ChildSpec(model=Section, fk="catalog", **extra),
    "ForwardRelationSpec": lambda **extra: ForwardRelationSpec(model=Author, **extra),
    "GenericRelationSpec": lambda **extra: GenericRelationSpec(model=Attachment, **extra),
    "ManyToManySpec": lambda **extra: ManyToManySpec(model=Tag, **extra),
    "ReverseOneToOneSpec": lambda **extra: ReverseOneToOneSpec(model=Profile, fk="author", **extra),
}


@pytest.mark.parametrize("kind", sorted(_KINDS))
def test_the_children_alias_is_gone(kind: str) -> None:
    """``relations=`` is the one spelling; ``children=`` was its alias."""
    with pytest.raises(TypeError, match="unexpected keyword argument 'children'"):
        _KINDS[kind](children={})


# Each kind with one argument past the ones that stay positional: the model,
# and for the two kinds a foreign key names, that key.
_ONE_TOO_MANY: dict[str, Callable[[], Any]] = {
    "ChildSpec": lambda: ChildSpec(Section, "catalog", "pk"),
    "ForwardRelationSpec": lambda: ForwardRelationSpec(Author, "pk"),
    "GenericRelationSpec": lambda: GenericRelationSpec(Attachment, "content_type"),
    "ManyToManySpec": lambda: ManyToManySpec(Tag, "pk"),
    "ReverseOneToOneSpec": lambda: ReverseOneToOneSpec(Profile, "author", {"bio": "bio"}),
}


@pytest.mark.parametrize("kind", sorted(_ONE_TOO_MANY))
def test_every_option_is_keyword_only(kind: str) -> None:
    """Only the model, and the foreign key where there is one, are positional,
    so a field added between two options cannot shift what a call meant."""
    with pytest.raises(TypeError, match="positional argument"):
        _ONE_TOO_MANY[kind]()
