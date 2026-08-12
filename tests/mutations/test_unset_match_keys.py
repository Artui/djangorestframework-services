"""A relation row whose match key was left ``UNSET`` is a create, not a match.

``UNSET`` means "field omitted from input" everywhere else in the library, and
:func:`filter_input` drops it before anything is assigned. The match-key reads
happen *before* that, so a partial-input dataclass -- a documented input shape,
and the whole reason the sentinel exists -- used to have its sentinel read as if
it were a key. Every kind that matches by key is covered here, because the same
omission was fatal for most combinations and accidentally harmless for one: a
non-pk ``match_key`` on an owned collection missed the ``existing_by_key`` lookup
and fell through to the create branch, arriving at the right answer for the
wrong reason.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rest_framework_services import (
    ChildSpec,
    ForwardRelationSpec,
    GenericRelationSpec,
    ManyToManySpec,
    ServiceValidationError,
    acreate_from_input,
    aupdate_from_input,
    create_from_input,
    update_from_input,
)
from rest_framework_services.types.unset import UNSET, UnsetType
from tests.testapp.models import Attachment, Author, Catalog, Post, Section, Tag


@dataclass
class SectionIn:
    """A partial-update row: ``pk`` omitted means "create me"."""

    pk: int | UnsetType = UNSET
    title: str | UnsetType = UNSET


@dataclass
class AuthorIn:
    pk: int | UnsetType = UNSET
    name: str | UnsetType = UNSET


@dataclass
class TagIn:
    pk: int | UnsetType = UNSET
    name: str | UnsetType = UNSET


@dataclass
class AttachmentIn:
    pk: int | UnsetType = UNSET
    label: str | UnsetType = UNSET


@pytest.mark.django_db
class TestAnOwnedCollectionCreatesTheRow:
    def test_a_child_with_an_unset_pk_is_created(self) -> None:
        catalog = Catalog.objects.create(name="c")

        result = update_from_input(
            catalog,
            {"sections": [SectionIn(title="s")]},
            children={"sections": ChildSpec(model=Section, fk="catalog")},
        )

        assert [change.created for change in result.children] == [(Section.objects.get().pk,)]
        assert Section.objects.get().title == "s"

    def test_it_matches_the_plain_mapping_that_omits_the_key(self) -> None:
        """The control: the sentinel has to mean what leaving the key out means."""
        spec = {"sections": ChildSpec(model=Section, fk="catalog")}
        sentinel = update_from_input(
            Catalog.objects.create(name="c"),
            {"sections": [SectionIn(title="s")]},
            children=spec,
        )
        omitted = update_from_input(
            Catalog.objects.create(name="c"),
            {"sections": [{"title": "s"}]},
            children=spec,
        )

        assert [len(change.created) for change in sentinel.children] == [
            len(change.created) for change in omitted.children
        ]

    def test_a_non_pk_match_key_is_created_for_the_right_reason(self) -> None:
        """Row 5 of the report: right before the fix, but by falling through."""
        catalog = Catalog.objects.create(name="c")

        @dataclass
        class ByTitle:
            title: str | UnsetType = UNSET

        result = update_from_input(
            catalog,
            {"sections": [ByTitle(title="s")]},
            children={"sections": ChildSpec(model=Section, fk="catalog", match_key="title")},
        )

        assert [change.created for change in result.children] == [(Section.objects.get().pk,)]

    def test_a_generic_relation_row_is_created(self) -> None:
        catalog = Catalog.objects.create(name="c")

        result = update_from_input(
            catalog,
            {"attachments": [AttachmentIn(label="a")]},
            relations={"attachments": GenericRelationSpec(model=Attachment)},
        )

        assert [change.created for change in result.children] == [(Attachment.objects.get().pk,)]

    def test_a_supplied_key_still_matches(self) -> None:
        """The sentinel is absent, not falsy: a real key still updates its row."""
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="old")

        result = update_from_input(
            catalog,
            {"sections": [SectionIn(pk=section.pk, title="new")]},
            children={"sections": ChildSpec(model=Section, fk="catalog")},
        )

        assert [change.updated for change in result.children] == [(section.pk,)]
        section.refresh_from_db()
        assert section.title == "new"


@pytest.mark.django_db
class TestAScopedTargetCreatesTheRow:
    def test_a_scoped_forward_target_is_created(self) -> None:
        """Row 2: the sentinel used to reach the queryset as an integer lookup."""
        result = create_from_input(
            Post,
            {"title": "t", "author": AuthorIn(name="a")},
            relations={"author": ForwardRelationSpec(model=Author, scope=Author.objects.all())},
        )

        assert result.instance.author == Author.objects.get()
        assert Author.objects.get().name == "a"

    def test_an_unscoped_forward_target_is_created(self) -> None:
        """Row 3: a payload carrying no key must not read as one that does.

        The unscoped guard exists for a row that *names* a target. A sentinel
        naming nothing used to trip it, so an omitted optional key raised
        ``ImproperlyConfigured`` -- a 500 telling the author to declare a
        ``scope=`` their create-only spec does not need.
        """
        result = create_from_input(
            Post,
            {"title": "t", "author": AuthorIn(name="a")},
            relations={"author": ForwardRelationSpec(model=Author)},
        )

        assert result.instance.author == Author.objects.get()

    def test_a_natural_match_key_is_created(self) -> None:
        """Row 4: a ``CharField`` key coerced the sentinel to the string "UNSET"."""

        @dataclass
        class ByName:
            name: str | UnsetType = UNSET

        result = create_from_input(
            Post,
            {"title": "t", "author": ByName()},
            relations={
                "author": ForwardRelationSpec(
                    model=Author, match_key="name", scope=Author.objects.all()
                )
            },
        )

        assert result.instance.author == Author.objects.get()

    def test_a_scoped_m2m_target_is_created(self) -> None:
        result = create_from_input(
            Post,
            {"title": "t", "tags": [TagIn(name="x")]},
            relations={"tags": ManyToManySpec(model=Tag, scope=Tag.objects.all())},
        )

        assert list(result.instance.tags.all()) == [Tag.objects.get()]

    def test_an_unscoped_m2m_target_is_created(self) -> None:
        result = create_from_input(
            Post,
            {"title": "t", "tags": [TagIn(name="x")]},
            relations={"tags": ManyToManySpec(model=Tag)},
        )

        assert list(result.instance.tags.all()) == [Tag.objects.get()]


@pytest.mark.django_db
class TestThePrimaryKeyGuardReadsTheSentinelAsAbsent:
    def test_an_unset_pk_does_not_trip_the_guard(self) -> None:
        """Row 1: the guard refused a payload that carried no primary key.

        Its own message said "omit the identifier to create a new one", which
        is exactly what the caller had done.
        """
        catalog = Catalog.objects.create(name="c")

        result = update_from_input(
            catalog,
            {"sections": [SectionIn(title="s")]},
            children={"sections": ChildSpec(model=Section, fk="catalog")},
        )

        assert [change.created for change in result.children] == [(Section.objects.get().pk,)]

    def test_a_real_unmatched_pk_is_still_refused(self) -> None:
        """The guard's actual job is untouched."""
        catalog = Catalog.objects.create(name="c")
        stranger = Section.objects.create(catalog=Catalog.objects.create(name="other"), title="s")

        with pytest.raises(ServiceValidationError) as excinfo:
            update_from_input(
                catalog,
                {"sections": [SectionIn(pk=stranger.pk, title="mine")]},
                children={"sections": ChildSpec(model=Section, fk="catalog")},
            )

        assert "which this write did not match" in excinfo.value.detail["sections"][0]


@pytest.mark.django_db(transaction=True)
class TestTheAsyncPathAgrees:
    async def test_an_async_child_with_an_unset_pk_is_created(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")

        result = await aupdate_from_input(
            catalog,
            {"sections": [SectionIn(title="s")]},
            children={"sections": ChildSpec(model=Section, fk="catalog")},
        )

        section = await Section.objects.aget()
        assert [change.created for change in result.children] == [(section.pk,)]

    async def test_an_async_scoped_target_is_created(self) -> None:
        result = await acreate_from_input(
            Post,
            {"title": "t", "author": AuthorIn(name="a")},
            relations={"author": ForwardRelationSpec(model=Author, scope=Author.objects.all())},
        )

        author = await Author.objects.aget()
        assert result.instance.author_id == author.pk
