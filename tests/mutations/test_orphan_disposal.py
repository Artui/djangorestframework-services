"""``orphan=`` — saying what happens to a row the relation lets go.

The default derives it from the link, as it always has. These assert that the
derived rule is unchanged, that stating it overrides the derivation in both
directions, that asking for an impossible one raises where the rule is read,
and that the ``delete_model`` cascade disposes of a row by the same rule as an
update — a flag meaning two things would be worse than no flag.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services import (
    ChildSpec,
    GenericRelationSpec,
    RelationOrphan,
    ReverseOneToOneSpec,
    aupdate_from_input,
    update_from_input,
)
from rest_framework_services.mutations.utils import adelete_relations, delete_relations
from tests.testapp.models import (
    Annotation,
    Attachment,
    Author,
    Catalog,
    Cover,
    Note,
    Profile,
    Section,
)


def _notes(**kwargs: Any) -> dict[str, ChildSpec]:
    """The nullable-FK collection: ``AUTO`` unlinks it."""
    return {"notes": ChildSpec(model=Note, fk="catalog", **kwargs)}


def _sections(**kwargs: Any) -> dict[str, ChildSpec]:
    """The non-nullable-FK collection: ``AUTO`` deletes it."""
    return {"sections": ChildSpec(model=Section, fk="catalog", **kwargs)}


@pytest.mark.django_db
class TestTheDerivedRuleIsUnchanged:
    def test_auto_unlinks_a_nullable_link(self) -> None:
        catalog = Catalog.objects.create(name="c")
        note = Note.objects.create(catalog=catalog, body="orphan")

        result = update_from_input(catalog, {"notes": []}, children=_notes())

        note.refresh_from_db()
        assert note.catalog_id is None
        assert result.get_child_change("notes").unlinked == (note.pk,)

    def test_auto_deletes_a_link_that_cannot_be_blanked(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="orphan")

        result = update_from_input(catalog, {"sections": []}, children=_sections())

        assert not Section.objects.filter(pk=section.pk).exists()
        assert result.get_child_change("sections").deleted == (section.pk,)


@pytest.mark.django_db
class TestStatingItOverridesTheDerivation:
    def test_delete_removes_a_row_auto_would_have_kept(self) -> None:
        catalog = Catalog.objects.create(name="c")
        note = Note.objects.create(catalog=catalog, body="orphan")

        result = update_from_input(
            catalog, {"notes": []}, children=_notes(orphan=RelationOrphan.DELETE)
        )

        # The FK is nullable, so the derived rule would have unlinked it.
        assert not Note.objects.filter(pk=note.pk).exists()
        assert result.get_child_change("notes").deleted == (note.pk,)
        assert result.get_child_change("notes").unlinked == ()

    def test_the_plain_string_works_as_the_member_does(self) -> None:
        catalog = Catalog.objects.create(name="c")
        note = Note.objects.create(catalog=catalog, body="orphan")

        update_from_input(catalog, {"notes": []}, children=_notes(orphan="delete"))

        assert not Note.objects.filter(pk=note.pk).exists()

    def test_unlink_keeps_a_row_it_could_also_have_kept_by_derivation(self) -> None:
        catalog = Catalog.objects.create(name="c")
        note = Note.objects.create(catalog=catalog, body="orphan")

        result = update_from_input(catalog, {"notes": []}, children=_notes(orphan="unlink"))

        note.refresh_from_db()
        assert note.catalog_id is None
        assert result.get_child_change("notes").unlinked == (note.pk,)

    def test_a_generic_relation_takes_the_same_flag(self) -> None:
        catalog = Catalog.objects.create(name="c")
        annotation = Annotation.objects.create(owner=catalog, text="orphan")

        result = update_from_input(
            catalog,
            {"annotations": []},
            relations={
                "annotations": GenericRelationSpec(
                    model=Annotation,
                    content_type_field="kind",
                    object_id_field="row_id",
                    orphan="delete",
                )
            },
        )

        # Both link columns are nullable, so AUTO would have blanked the pair.
        assert not Annotation.objects.filter(pk=annotation.pk).exists()
        assert result.get_child_change("annotations").deleted == (annotation.pk,)

    def test_a_reverse_one_to_one_takes_the_same_flag(self) -> None:
        author = Author.objects.create(name="a")
        profile = Profile.objects.create(author=author, bio="b")

        result = update_from_input(
            author,
            {"profile": None},
            relations={"profile": ReverseOneToOneSpec(model=Profile, fk="author", orphan="delete")},
        )

        assert not Profile.objects.filter(pk=profile.pk).exists()
        assert result.get_relation_change("profile").outcome == "deleted"


@pytest.mark.django_db
class TestUnlinkNeedsALinkItCanBlank:
    def test_a_collection_raises_and_removes_nothing(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="orphan")

        with pytest.raises(ImproperlyConfigured) as excinfo:
            update_from_input(catalog, {"sections": []}, children=_sections(orphan="unlink"))

        message = str(excinfo.value)
        assert "relations['sections']" in message  # the relation
        assert "Section.catalog cannot hold NULL" in message  # the field
        assert "null=True" in message  # and both remedies
        assert "orphan='delete'" in message
        assert Section.objects.filter(pk=section.pk).exists()

    def test_a_generic_relation_names_every_column_that_cannot_be_blanked(self) -> None:
        catalog = Catalog.objects.create(name="c")
        Attachment.objects.create(owner=catalog, label="orphan")

        with pytest.raises(ImproperlyConfigured) as excinfo:
            update_from_input(
                catalog,
                {"attachments": []},
                relations={"attachments": GenericRelationSpec(model=Attachment, orphan="unlink")},
            )

        assert "Attachment.content_type, Attachment.object_id" in str(excinfo.value)

    def test_a_reverse_one_to_one_raises_too(self) -> None:
        catalog = Catalog.objects.create(name="c")
        Cover.objects.create(catalog=catalog, image="i")

        with pytest.raises(ImproperlyConfigured, match="Cover.catalog"):
            update_from_input(
                catalog,
                {"cover": None},
                relations={
                    "cover": ReverseOneToOneSpec(model=Cover, fk="catalog", orphan="unlink")
                },
            )

    def test_a_merge_relation_disposes_of_nothing_so_the_rule_is_never_read(self) -> None:
        # ``mode`` says whether a row is let go at all; ``orphan`` only says
        # what happens to one that is. Nothing is let go here, so there is
        # nothing for the impossible rule to be wrong about.
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="kept")

        update_from_input(
            catalog, {"sections": []}, children=_sections(mode="merge", orphan="unlink")
        )

        assert Section.objects.filter(pk=section.pk).exists()


@pytest.mark.django_db
class TestTheCascadeDisposesTheSameWay:
    def test_a_collection_follows_the_flag(self) -> None:
        catalog = Catalog.objects.create(name="c")
        note = Note.objects.create(catalog=catalog, body="n")

        deltas, _ = delete_relations(catalog, _notes(orphan="delete"))

        assert not Note.objects.filter(pk=note.pk).exists()
        assert deltas[0].deleted == (note.pk,)

    def test_a_collection_still_derives_it_by_default(self) -> None:
        catalog = Catalog.objects.create(name="c")
        note = Note.objects.create(catalog=catalog, body="n")

        deltas, _ = delete_relations(catalog, _notes())

        note.refresh_from_db()
        assert note.catalog_id is None
        assert deltas[0].unlinked == (note.pk,)

    def test_a_reverse_one_to_one_follows_the_flag(self) -> None:
        author = Author.objects.create(name="a")
        profile = Profile.objects.create(author=author, bio="b")

        _, singular = delete_relations(
            author,
            {"profile": ReverseOneToOneSpec(model=Profile, fk="author", orphan="delete")},
        )

        assert not Profile.objects.filter(pk=profile.pk).exists()
        assert singular[0].outcome == "deleted"

    def test_an_impossible_unlink_raises_here_as_well(self) -> None:
        catalog = Catalog.objects.create(name="c")
        Section.objects.create(catalog=catalog, title="s")

        with pytest.raises(ImproperlyConfigured, match="Section.catalog"):
            delete_relations(catalog, _sections(orphan="unlink"))


@pytest.mark.django_db(transaction=True)
class TestTheAsyncPathDisposesIdentically:
    async def test_an_orphan_follows_the_flag(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        note = await Note.objects.acreate(catalog=catalog, body="orphan")

        result = await aupdate_from_input(catalog, {"notes": []}, children=_notes(orphan="delete"))

        assert not await Note.objects.filter(pk=note.pk).aexists()
        assert result.get_child_change("notes").deleted == (note.pk,)

    async def test_an_impossible_unlink_raises(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        await Section.objects.acreate(catalog=catalog, title="s")

        with pytest.raises(ImproperlyConfigured, match="Section.catalog"):
            await aupdate_from_input(catalog, {"sections": []}, children=_sections(orphan="unlink"))

    async def test_the_cascade_follows_the_flag(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        note = await Note.objects.acreate(catalog=catalog, body="n")

        deltas, _ = await adelete_relations(catalog, _notes(orphan="delete"))

        assert not await Note.objects.filter(pk=note.pk).aexists()
        assert deltas[0].deleted == (note.pk,)

    async def test_a_reverse_one_to_one_cascade_follows_the_flag(self) -> None:
        author = await Author.objects.acreate(name="a")
        profile = await Profile.objects.acreate(author=author, bio="b")

        _, singular = await adelete_relations(
            author,
            {"profile": ReverseOneToOneSpec(model=Profile, fk="author", orphan="delete")},
        )

        assert not await Profile.objects.filter(pk=profile.pk).aexists()
        assert singular[0].outcome == "deleted"

    async def test_a_reverse_one_to_one_removal_follows_the_flag(self) -> None:
        author = await Author.objects.acreate(name="a")
        profile = await Profile.objects.acreate(author=author, bio="b")

        result = await aupdate_from_input(
            author,
            {"profile": None},
            relations={"profile": ReverseOneToOneSpec(model=Profile, fk="author", orphan="delete")},
        )

        assert not await Profile.objects.filter(pk=profile.pk).aexists()
        assert result.get_relation_change("profile").outcome == "deleted"
