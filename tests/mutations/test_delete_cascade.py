"""The delete cascade, one rule across every relation kind.

The cascade removes the rows the parent **owns** and leaves alone the rows it
merely points at. Ownership is the whole question, and each kind answers it the
same way here as on the write path -- which is why this is settled once, for
all of them, rather than a rule per kind.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services import (
    ChildSpec,
    ForwardRelationSpec,
    GenericRelationSpec,
    ManyToManySpec,
    RelationPhase,
    RelationSpec,
    ReverseOneToOneSpec,
    adelete_model,
    delete_model,
)
from rest_framework_services.mutations.utils import adelete_relations, delete_relations
from tests.testapp.models import (
    Annotation,
    Attachment,
    Author,
    Catalog,
    Cover,
    Item,
    Note,
    Post,
    Profile,
    Section,
    Tag,
)


class _UnknownKind(RelationSpec):
    write_phase = RelationPhase.M2M


@pytest.mark.django_db
class TestWhatTheParentOwnsGoes:
    def test_a_generic_relation_is_removed_like_a_child_collection(self) -> None:
        catalog = Catalog.objects.create(name="c")
        attachment = catalog.attachments.create(label="a")
        annotation = catalog.annotations.create(text="n")

        collections, singular = delete_relations(
            catalog,
            {
                "attachments": GenericRelationSpec(model=Attachment),
                "annotations": GenericRelationSpec(
                    model=Annotation, content_type_field="kind", object_id_field="row_id"
                ),
            },
        )

        assert singular == ()
        assert collections[0].deleted == (attachment.pk,)
        # Nullable link -> unlinked, exactly as a nullable foreign key is.
        assert collections[1].unlinked == (annotation.pk,)
        assert not Attachment.objects.exists()
        annotation.refresh_from_db()
        assert (annotation.kind_id, annotation.row_id) == (None, None)

    def test_a_reverse_one_to_one_reports_as_one_row(self) -> None:
        author = Author.objects.create(name="a")
        profile = Profile.objects.create(author=author, bio="b")

        _, singular = delete_relations(
            author, {"profile": ReverseOneToOneSpec(model=Profile, fk="author")}
        )

        # Nullable FK on Profile.author -> unlinked, not deleted.
        assert (singular[0].outcome, singular[0].pk) == ("unlinked", profile.pk)
        profile.refresh_from_db()
        assert profile.author_id is None

    def test_a_reverse_one_to_one_the_parent_does_not_have_is_untouched(self) -> None:
        author = Author.objects.create(name="a")

        _, singular = delete_relations(
            author, {"profile": ReverseOneToOneSpec(model=Profile, fk="author")}
        )

        assert not singular[0]
        assert singular[0].outcome == "untouched"

    def test_the_rows_own_relations_go_first(self) -> None:
        # A grandchild declared through relations= is cascaded exactly as one
        # declared through children=: the cascade follows the same tree the
        # write path does.
        author = Author.objects.create(name="a")
        profile = Profile.objects.create(author=author, bio="b")
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="s")
        item = Item.objects.create(section=section, label="i")

        delete_relations(
            catalog,
            {
                "sections": ChildSpec(
                    model=Section,
                    fk="catalog",
                    relations={"items": ChildSpec(model=Item, fk="section")},
                )
            },
        )
        delete_relations(
            author,
            {
                "profile": ReverseOneToOneSpec(
                    model=Profile,
                    fk="author",
                    relations={},
                )
            },
        )

        assert not Item.objects.filter(pk=item.pk).exists()
        assert not Section.objects.filter(pk=section.pk).exists()
        profile.refresh_from_db()
        assert profile.author_id is None


@pytest.mark.django_db
class TestWhatTheParentOnlyPointsAtStays:
    def test_a_many_to_many_loses_the_membership_and_nothing_else(self) -> None:
        post = Post.objects.create(title="t")
        tag = Tag.objects.create(name="shared")
        other = Post.objects.create(title="other")
        post.tags.add(tag)
        other.tags.add(tag)

        collections, _ = delete_relations(post, {"tags": ManyToManySpec(model=Tag)})

        assert collections[0].unlinked == (tag.pk,)
        assert (collections[0].deleted, collections[0].removed) == ((), ())
        assert post.tags.count() == 0
        # The row is shared -- the other parent still has it.
        assert list(other.tags.all()) == [tag]

    def test_a_forward_relation_is_reported_untouched(self) -> None:
        author = Author.objects.create(name="a")
        post = Post.objects.create(title="t", author=author)

        _, singular = delete_relations(post, {"author": ForwardRelationSpec(model=Author)})

        assert (singular[0].relation, singular[0].outcome) == ("author", "untouched")
        assert not singular[0]
        # Nothing for the cascade to do: the column holding the link goes when
        # the parent row does, and the target may be shared.
        assert Author.objects.filter(pk=author.pk).exists()

    def test_an_unknown_kind_is_still_refused(self) -> None:
        catalog = Catalog.objects.create(name="c")

        with pytest.raises(ImproperlyConfigured, match=r"relations\['mystery'\]: _UnknownKind"):
            delete_relations(catalog, {"mystery": _UnknownKind()})


@pytest.mark.django_db
class TestTheDefaultDeleteServiceTakesTheSameMap:
    def test_children_and_relations_are_folded_into_one_cascade(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="s")
        note = Note.objects.create(catalog=catalog, body="n")
        attachment = catalog.attachments.create(label="a")

        delete_model(
            Catalog,
            children={"sections": ChildSpec(model=Section, fk="catalog")},
            relations={
                "notes": ChildSpec(model=Note, fk="catalog"),
                "attachments": GenericRelationSpec(model=Attachment),
            },
        )(instance=catalog)

        assert not Section.objects.filter(pk=section.pk).exists()
        assert not Attachment.objects.filter(pk=attachment.pk).exists()
        note.refresh_from_db()
        assert note.catalog_id is None
        assert not Catalog.objects.exists()

    def test_a_soft_delete_still_gets_the_cascade(self) -> None:
        # The reason the cascade exists at all: Django never cascades through a
        # soft delete, because no row is deleted.
        catalog = Catalog.objects.create(name="c")
        catalog.attachments.create(label="a")
        archived: list[Catalog] = []

        delete_model(
            Catalog,
            soft_delete=archived.append,
            relations={"attachments": GenericRelationSpec(model=Attachment)},
        )(instance=catalog)

        assert archived == [catalog]
        assert Catalog.objects.filter(pk=catalog.pk).exists()
        assert not Attachment.objects.exists()

    def test_declaring_nothing_deletes_only_the_parent(self) -> None:
        catalog = Catalog.objects.create(name="c")

        delete_model(Catalog)(instance=catalog)

        assert not Catalog.objects.exists()

    def test_a_name_in_both_maps_is_refused_at_construction(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="also declared in children="):
            delete_model(
                Catalog,
                children={"sections": ChildSpec(model=Section, fk="catalog")},
                relations={"sections": ChildSpec(model=Section, fk="catalog")},
            )


@pytest.mark.django_db(transaction=True)
class TestTheAsyncCascadeFollowsTheSameRule:
    async def test_owned_rows_go_and_shared_rows_stay(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        section = await Section.objects.acreate(catalog=catalog, title="s")
        await Item.objects.acreate(section=section, label="i")
        cover = await Cover.objects.acreate(catalog=catalog, image="i")
        tag = await Tag.objects.acreate(name="shared")
        await section.tags.aadd(tag)

        collections, singular = await adelete_relations(
            catalog,
            {
                "sections": ChildSpec(
                    model=Section,
                    fk="catalog",
                    children={"items": ChildSpec(model=Item, fk="section")},
                    relations={"tags": ManyToManySpec(model=Tag)},
                ),
                "cover": ReverseOneToOneSpec(model=Cover, fk="catalog"),
            },
        )

        assert collections[0].deleted == (section.pk,)
        assert (singular[0].outcome, singular[0].pk) == ("deleted", cover.pk)
        assert not await Item.objects.aexists()
        assert await Tag.objects.filter(pk=tag.pk).aexists()

    async def test_a_generic_relation_and_a_forward_one(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        author = await Author.objects.acreate(name="a")
        attachment = await Attachment.objects.acreate(
            label="a", content_type=await _content_type(Catalog), object_id=catalog.pk
        )

        collections, singular = await adelete_relations(
            catalog,
            {
                "attachments": GenericRelationSpec(model=Attachment),
                "owner": ForwardRelationSpec(model=Author),
            },
        )

        assert collections[0].deleted == (attachment.pk,)
        assert singular[0].outcome == "untouched"
        assert await Author.objects.filter(pk=author.pk).aexists()

    async def test_a_reverse_one_to_one_the_parent_does_not_have(self) -> None:
        author = await Author.objects.acreate(name="a")

        _, singular = await adelete_relations(
            author, {"profile": ReverseOneToOneSpec(model=Profile, fk="author")}
        )

        assert not singular[0]

    async def test_an_unknown_kind_is_still_refused(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")

        with pytest.raises(ImproperlyConfigured, match=r"relations\['mystery'\]"):
            await adelete_relations(catalog, {"mystery": _UnknownKind()})

    async def test_the_default_async_service_folds_both_maps(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        note = await Note.objects.acreate(catalog=catalog, body="n")

        await adelete_model(
            Catalog,
            relations={"notes": ChildSpec(model=Note, fk="catalog")},
        )(instance=catalog)

        await note.arefresh_from_db()
        assert note.catalog_id is None
        assert not await Catalog.objects.aexists()

    async def test_the_async_service_declaring_nothing_deletes_only_the_parent(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")

        await adelete_model(Catalog)(instance=catalog)

        assert not await Catalog.objects.aexists()


async def _content_type(model: type[Any]) -> Any:
    """The parent's content type, fetched off the event loop."""
    from asgiref.sync import sync_to_async
    from django.contrib.contenttypes.models import ContentType

    return await sync_to_async(ContentType.objects.get_for_model, thread_sensitive=True)(model)
