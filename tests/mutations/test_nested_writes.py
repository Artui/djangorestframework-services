"""Reverse-FK child-collection writes (NEST) through the mutation helpers."""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_services import (
    ChildSpec,
    ManyToManySpec,
    acreate_from_input,
    aupdate_from_input,
    create_from_input,
    update_from_input,
)
from rest_framework_services.mutations.utils import adelete_relations, delete_relations
from tests.testapp.models import Catalog, Item, Note, Section, Tag

_SECTIONS = "sections"


def _spec(**kw: Any) -> dict[str, ChildSpec]:
    return {_SECTIONS: ChildSpec(model=Section, fk="catalog", **kw)}


@pytest.mark.django_db
class TestCreateChildren:
    def test_creates_children_and_grandchildren(self) -> None:
        result = create_from_input(
            Catalog,
            {
                "name": "c",
                "sections": [
                    {"title": "s1", "items": [{"label": "i1"}, {"label": "i2"}]},
                ],
            },
            children={
                _SECTIONS: ChildSpec(
                    model=Section,
                    fk="catalog",
                    children={"items": ChildSpec(model=Item, fk="section")},
                ),
            },
        )
        catalog = result.instance
        section = catalog.sections.get()
        assert section.title == "s1"
        assert set(section.items.values_list("label", flat=True)) == {"i1", "i2"}
        # Parent delta reports the direct collection only.
        change = result.get_child_change("sections")
        assert change is not None
        assert change.created == (section.pk,)
        assert bool(result) is True

    def test_child_m2m_via_callable(self) -> None:
        tag = Tag.objects.create(name="t")
        result = create_from_input(
            Catalog,
            {"name": "c", "sections": [{"title": "s", "tags": [tag.pk]}]},
            children={
                _SECTIONS: ChildSpec(
                    model=Section,
                    fk="catalog",
                    exclude_fields=["tags"],
                    m2m=lambda row: {"tags": row.get("tags", [])},
                ),
            },
        )
        assert list(result.instance.sections.get().tags.all()) == [tag]

    def test_omitted_relation_creates_nothing(self) -> None:
        result = create_from_input(Catalog, {"name": "c"}, children=_spec())
        assert result.instance.sections.count() == 0
        assert not result.get_child_change("sections")


@pytest.mark.django_db
class TestUpdateReplace:
    def test_create_update_and_delete_orphan(self) -> None:
        catalog = Catalog.objects.create(name="c")
        keep = Section.objects.create(catalog=catalog, title="keep")
        orphan = Section.objects.create(catalog=catalog, title="orphan")
        result = update_from_input(
            catalog,
            {"sections": [{"pk": keep.pk, "title": "kept"}, {"title": "new"}]},
            children=_spec(),
        )
        keep.refresh_from_db()
        assert keep.title == "kept"
        assert not Section.objects.filter(pk=orphan.pk).exists()  # non-nullable → deleted
        assert set(catalog.sections.values_list("title", flat=True)) == {"kept", "new"}
        change = result.get_child_change("sections")
        assert change is not None
        assert change.updated == (keep.pk,)
        assert change.deleted == (orphan.pk,)
        assert len(change.created) == 1

    def test_nullable_orphan_is_unlinked_not_deleted(self) -> None:
        catalog = Catalog.objects.create(name="c")
        keep = Note.objects.create(catalog=catalog, body="keep")
        orphan = Note.objects.create(catalog=catalog, body="orphan")
        result = update_from_input(
            catalog,
            {"notes": [{"pk": keep.pk, "body": "keep"}]},
            children={"notes": ChildSpec(model=Note, fk="catalog")},
        )
        orphan.refresh_from_db()
        assert orphan.catalog_id is None  # nullable → unlinked
        assert Note.objects.filter(pk=orphan.pk).exists()
        change = result.get_child_change("notes")
        assert change is not None
        assert change.unlinked == (orphan.pk,)
        assert change.deleted == ()

    def test_empty_list_removes_all(self) -> None:
        catalog = Catalog.objects.create(name="c")
        Section.objects.create(catalog=catalog, title="a")
        update_from_input(catalog, {"sections": []}, children=_spec())
        assert catalog.sections.count() == 0

    def test_omitted_relation_left_untouched(self) -> None:
        catalog = Catalog.objects.create(name="c")
        Section.objects.create(catalog=catalog, title="a")
        result = update_from_input(catalog, {"name": "c2"}, children=_spec())
        assert catalog.sections.count() == 1
        catalog.refresh_from_db()
        assert catalog.name == "c2"
        assert not result.get_child_change("sections")

    def test_custom_match_key(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="alpha")
        result = update_from_input(
            catalog,
            {"sections": [{"title": "alpha"}]},
            children=_spec(match_key="title"),
        )
        assert catalog.sections.count() == 1  # matched by title → updated, not duplicated
        change = result.get_child_change("sections")
        assert change is not None
        assert change.updated == (section.pk,)

    def test_children_only_change_is_truthy(self) -> None:
        catalog = Catalog.objects.create(name="c")
        result = update_from_input(
            catalog, {"name": "c", "sections": [{"title": "s"}]}, children=_spec()
        )
        assert result.changes == ()  # name unchanged
        assert bool(result) is True  # but a child was created

    def test_get_child_change_lookup(self) -> None:
        catalog = Catalog.objects.create(name="c")
        result = update_from_input(
            catalog,
            {"sections": [], "notes": []},
            children={
                "sections": ChildSpec(model=Section, fk="catalog"),
                "notes": ChildSpec(model=Note, fk="catalog"),
            },
        )
        # "notes" is the second collection — the lookup loop skips "sections".
        assert result.get_child_change("notes") is not None
        # An unconfigured relation returns None.
        assert result.get_child_change("missing") is None


@pytest.mark.django_db
class TestUpdateMerge:
    def test_upserts_without_removing_orphans(self) -> None:
        catalog = Catalog.objects.create(name="c")
        orphan = Section.objects.create(catalog=catalog, title="orphan")
        result = update_from_input(
            catalog, {"sections": [{"title": "new"}]}, children=_spec(mode="merge")
        )
        assert Section.objects.filter(pk=orphan.pk).exists()  # merge never deletes
        change = result.get_child_change("sections")
        assert change is not None
        assert change.deleted == () and change.unlinked == ()
        assert len(change.created) == 1


@pytest.mark.django_db
class TestDeleteChildren:
    def test_cascades_recursively_and_unlinks_nullable(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="s")
        item = Item.objects.create(section=section, label="i")
        note = Note.objects.create(catalog=catalog, body="n")
        deltas, _ = delete_relations(
            catalog,
            {
                _SECTIONS: ChildSpec(
                    model=Section,
                    fk="catalog",
                    children={"items": ChildSpec(model=Item, fk="section")},
                ),
                "notes": ChildSpec(model=Note, fk="catalog"),
            },
        )
        assert not Section.objects.filter(pk=section.pk).exists()
        assert not Item.objects.filter(pk=item.pk).exists()  # grandchild removed first
        note.refresh_from_db()
        assert note.catalog_id is None  # nullable → unlinked
        by_relation = {d.relation: d for d in deltas}
        assert by_relation["sections"].deleted == (section.pk,)
        assert by_relation["notes"].unlinked == (note.pk,)


@pytest.mark.django_db
class TestWhatAPrefetchedCollectionReads:
    """A `prefetch_related` cache must not outlive the membership it recorded.

    The singular twin of this is
    ``test_singular_relations.TestTheInstanceReadsWhatWasWritten``. Collections
    differ in that they are reconciled *inside the parent's own accessor*, so an
    update writes the very objects the cache is holding -- which is why the rule
    here is keyed on membership rather than on the relation having been written.
    """

    def test_a_created_row_invalidates_the_cache(self) -> None:
        catalog = Catalog.objects.create(name="c")
        Section.objects.create(catalog=catalog, title="old")
        catalog = Catalog.objects.prefetch_related(_SECTIONS).get()
        assert [s.title for s in catalog.sections.all()] == ["old"]

        result = update_from_input(
            catalog, {"sections": [{"title": "new"}]}, children=_spec(mode="merge")
        )

        assert sorted(s.title for s in result.instance.sections.all()) == ["new", "old"]

    def test_a_removed_row_invalidates_the_cache(self) -> None:
        catalog = Catalog.objects.create(name="c")
        keep = Section.objects.create(catalog=catalog, title="keep")
        Section.objects.create(catalog=catalog, title="orphan")
        catalog = Catalog.objects.prefetch_related(_SECTIONS).get()
        assert catalog.sections.count() == 2

        result = update_from_input(
            catalog, {"sections": [{"pk": keep.pk, "title": "keep"}]}, children=_spec()
        )

        assert [s.title for s in result.instance.sections.all()] == ["keep"]

    def test_an_update_in_place_keeps_the_cache_and_its_query(
        self, django_assert_num_queries: Any
    ) -> None:
        # The rows were matched inside the accessor, so the write mutated the
        # cached objects themselves. Dropping the cache here would cost a query
        # to re-read what it already holds.
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="old")
        catalog = Catalog.objects.prefetch_related(_SECTIONS).get()
        assert [s.title for s in catalog.sections.all()] == ["old"]

        result = update_from_input(
            catalog, {"sections": [{"pk": section.pk, "title": "new"}]}, children=_spec()
        )

        with django_assert_num_queries(0):
            assert [s.title for s in result.instance.sections.all()] == ["new"]

    def test_an_omitted_relation_keeps_the_cache(self, django_assert_num_queries: Any) -> None:
        catalog = Catalog.objects.create(name="c")
        Section.objects.create(catalog=catalog, title="old")
        catalog = Catalog.objects.prefetch_related(_SECTIONS).get()
        assert [s.title for s in catalog.sections.all()] == ["old"]

        result = update_from_input(catalog, {"name": "c2"}, children=_spec())

        with django_assert_num_queries(0):
            assert [s.title for s in result.instance.sections.all()] == ["old"]

    def test_an_update_service_invalidates_the_cache(self) -> None:
        # The library did not do the writing, so it cannot vouch for which
        # object the service touched.
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="old")
        catalog = Catalog.objects.prefetch_related(_SECTIONS).get()
        assert [s.title for s in catalog.sections.all()] == ["old"]

        def rename(*, instance: Section, data: Any, **_: Any) -> Section:
            # Deliberately writes a row object the cache is not holding.
            fresh = Section.objects.get(pk=instance.pk)
            fresh.title = data["title"]
            fresh.save(update_fields=["title"])
            return fresh

        result = update_from_input(
            catalog,
            {"sections": [{"pk": section.pk, "title": "new"}]},
            children=_spec(update_service=rename),
        )

        assert [s.title for s in result.instance.sections.all()] == ["new"]

    def test_a_many_to_many_invalidates_on_any_write(self) -> None:
        # Matched in scope=, never in the parent's membership, so even a pure
        # field update writes a different object than the cache is holding.
        # This one guards the contract rather than this library's arm of the
        # rule: writing an m2m goes through manager.set(), and Django drops the
        # prefetch cache on its own way through.
        tag = Tag.objects.create(name="old")
        section = Section.objects.create(catalog=Catalog.objects.create(name="c"), title="s")
        section.tags.add(tag)
        section = Section.objects.prefetch_related("tags").get()
        assert [t.name for t in section.tags.all()] == ["old"]

        result = update_from_input(
            section,
            {"tags": [{"pk": tag.pk, "name": "new"}]},
            relations={"tags": ManyToManySpec(model=Tag, scope=Tag.objects.all())},
        )

        assert [t.name for t in result.instance.tags.all()] == ["new"]

    def test_a_soft_deleted_parent_outlives_its_own_cascade(self) -> None:
        # delete_relations does not delete the parent, and a soft_delete flow
        # renders exactly that row afterwards.
        catalog = Catalog.objects.create(name="c")
        Section.objects.create(catalog=catalog, title="s")
        catalog = Catalog.objects.prefetch_related(_SECTIONS).get()
        assert catalog.sections.count() == 1

        delete_relations(catalog, _spec())

        assert list(catalog.sections.all()) == []


@pytest.mark.django_db(transaction=True)
class TestAsyncChildren:
    async def test_acreate_with_children_and_m2m(self) -> None:
        tag = await Tag.objects.acreate(name="t")
        result = await acreate_from_input(
            Catalog,
            {
                "name": "c",
                "sections": [
                    {"title": "s", "tags": [tag.pk], "items": [{"label": "i"}]},
                ],
            },
            children={
                _SECTIONS: ChildSpec(
                    model=Section,
                    fk="catalog",
                    exclude_fields=["tags"],
                    m2m=lambda row: {"tags": row.get("tags", [])},
                    children={"items": ChildSpec(model=Item, fk="section")},
                ),
            },
        )
        assert await result.instance.sections.acount() == 1
        assert await Item.objects.acount() == 1
        section = await Section.objects.aget()
        assert await section.tags.acount() == 1

    async def test_aupdate_replace_deletes_and_unlinks(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        keep = await Section.objects.acreate(catalog=catalog, title="keep")
        section_orphan = await Section.objects.acreate(catalog=catalog, title="orphan")
        note_orphan = await Note.objects.acreate(catalog=catalog, body="n")
        result = await aupdate_from_input(
            catalog,
            {
                "sections": [{"pk": keep.pk, "title": "kept"}, {"title": "new"}],
                "notes": [],
            },
            children={
                _SECTIONS: ChildSpec(model=Section, fk="catalog"),
                "notes": ChildSpec(model=Note, fk="catalog"),
            },
        )
        assert not await Section.objects.filter(pk=section_orphan.pk).aexists()
        await note_orphan.arefresh_from_db()
        assert note_orphan.catalog_id is None
        change = result.get_child_change("sections")
        assert change is not None
        assert change.updated == (keep.pk,)
        assert change.deleted == (section_orphan.pk,)

    async def test_aupdate_merge_keeps_orphans(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        orphan = await Section.objects.acreate(catalog=catalog, title="orphan")
        await aupdate_from_input(
            catalog, {"sections": [{"title": "new"}]}, children=_spec(mode="merge")
        )
        assert await Section.objects.filter(pk=orphan.pk).aexists()

    async def test_aupdate_omitted_relation_untouched(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        await Section.objects.acreate(catalog=catalog, title="a")
        await aupdate_from_input(catalog, {"name": "c2"}, children=_spec())
        assert await catalog.sections.acount() == 1

    async def test_a_prefetched_collection_reads_what_was_written(self) -> None:
        # The sync twin is TestWhatAPrefetchedCollectionReads; the async path
        # reconciles the same way, so it goes stale the same way.
        catalog = await Catalog.objects.acreate(name="c")
        section = await Section.objects.acreate(catalog=catalog, title="old")
        catalog = await Catalog.objects.prefetch_related(_SECTIONS).aget()
        assert [s.title async for s in catalog.sections.all()] == ["old"]

        added = await aupdate_from_input(
            catalog, {"sections": [{"title": "new"}]}, children=_spec(mode="merge")
        )
        assert sorted([s.title async for s in added.instance.sections.all()]) == ["new", "old"]

        # An update in place keeps the prefetch, exactly as on the sync path.
        catalog = await Catalog.objects.prefetch_related(_SECTIONS).aget()
        assert len([s async for s in catalog.sections.all()]) == 2
        updated = await aupdate_from_input(
            catalog,
            {"sections": [{"pk": section.pk, "title": "renamed"}]},
            children=_spec(mode="merge"),
        )
        assert "renamed" in [s.title async for s in updated.instance.sections.all()]

    async def test_adelete_relations_recursive(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        section = await Section.objects.acreate(catalog=catalog, title="s")
        await Item.objects.acreate(section=section, label="i")
        note = await Note.objects.acreate(catalog=catalog, body="n")
        deltas, _ = await adelete_relations(
            catalog,
            {
                _SECTIONS: ChildSpec(
                    model=Section,
                    fk="catalog",
                    children={"items": ChildSpec(model=Item, fk="section")},
                ),
                "notes": ChildSpec(model=Note, fk="catalog"),
            },
        )
        assert not await Section.objects.aexists()
        assert not await Item.objects.aexists()
        await note.arefresh_from_db()
        assert note.catalog_id is None
        by_relation = {d.relation: d for d in deltas}
        assert by_relation["sections"].deleted == (section.pk,)
        assert by_relation["notes"].unlinked == (note.pk,)
