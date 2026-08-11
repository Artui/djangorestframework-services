"""Reverse-FK child-collection writes (NEST) through the mutation helpers."""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_services import (
    ChildSpec,
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
