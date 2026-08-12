"""The opaque ``context=`` thread through the mutation helpers.

``context`` exists so per-row work downstream can see the acting caller. The
helpers themselves must never read it, so every assertion here is "passing a
hostile mapping changes nothing".
"""

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
from tests.testapp.models import Catalog, Item, Note, Section

# Every key the child loop seeds for itself, plus a few of the helpers' own
# keyword names. If any of them were read rather than forwarded, one of the
# assertions below would move.
_JUNK: dict[str, Any] = {
    "data": "not-the-payload",
    "instance": "not-an-instance",
    "parent": "not-the-parent",
    "children": "not-a-spec-map",
    "model": "not-a-model",
    "m2m": "not-a-mapping",
    "field_map": "not-a-map",
    "user": "someone",
}

_SECTIONS = {"sections": ChildSpec(model=Section, fk="catalog")}


@pytest.mark.django_db
class TestSyncHelpersIgnoreContext:
    def test_create_without_children(self) -> None:
        result = create_from_input(Catalog, {"name": "c"}, context=_JUNK)
        assert result.instance.name == "c"
        assert result.created is True
        assert result.changed_fields == ("name",)
        assert result.children == ()

    def test_update_without_children(self) -> None:
        catalog = Catalog.objects.create(name="c")
        result = update_from_input(catalog, {"name": "c2"}, context=_JUNK)
        catalog.refresh_from_db()
        assert catalog.name == "c2"
        assert result.changed_fields == ("name",)

    def test_create_with_children_but_no_service(self) -> None:
        result = create_from_input(
            Catalog,
            {"name": "c", "sections": [{"title": "s"}]},
            children=_SECTIONS,
            context=_JUNK,
        )
        assert result.instance.sections.get().title == "s"

    def test_update_reconciliation_is_unaffected(self) -> None:
        catalog = Catalog.objects.create(name="c")
        keep = Section.objects.create(catalog=catalog, title="keep")
        orphan = Section.objects.create(catalog=catalog, title="orphan")
        result = update_from_input(
            catalog,
            {"sections": [{"pk": keep.pk, "title": "kept"}, {"title": "new"}]},
            children=_SECTIONS,
            context=_JUNK,
        )
        keep.refresh_from_db()
        assert keep.title == "kept"
        assert not Section.objects.filter(pk=orphan.pk).exists()
        change = result.get_child_change("sections")
        assert change is not None
        assert change.updated == (keep.pk,)
        assert change.deleted == (orphan.pk,)

    def test_delete_relations(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="s")
        Item.objects.create(section=section, label="i")
        note = Note.objects.create(catalog=catalog, body="n")
        deltas, _ = delete_relations(
            catalog,
            {
                "sections": ChildSpec(
                    model=Section,
                    fk="catalog",
                    children={"items": ChildSpec(model=Item, fk="section")},
                ),
                "notes": ChildSpec(model=Note, fk="catalog"),
            },
            context=_JUNK,
        )
        assert not Item.objects.exists()
        note.refresh_from_db()
        assert note.catalog_id is None
        by_relation = {d.relation: d for d in deltas}
        assert by_relation["sections"].deleted == (section.pk,)
        assert by_relation["notes"].unlinked == (note.pk,)


@pytest.mark.django_db(transaction=True)
class TestAsyncHelpersIgnoreContext:
    async def test_acreate_with_grandchildren(self) -> None:
        result = await acreate_from_input(
            Catalog,
            {"name": "c", "sections": [{"title": "s", "items": [{"label": "i"}]}]},
            children={
                "sections": ChildSpec(
                    model=Section,
                    fk="catalog",
                    children={"items": ChildSpec(model=Item, fk="section")},
                ),
            },
            context=_JUNK,
        )
        assert await result.instance.sections.acount() == 1
        assert await Item.objects.acount() == 1

    async def test_aupdate_reconciliation_is_unaffected(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        orphan = await Section.objects.acreate(catalog=catalog, title="orphan")
        await aupdate_from_input(
            catalog, {"sections": [{"title": "new"}]}, children=_SECTIONS, context=_JUNK
        )
        assert not await Section.objects.filter(pk=orphan.pk).aexists()

    async def test_adelete_relations(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        section = await Section.objects.acreate(catalog=catalog, title="s")
        await Item.objects.acreate(section=section, label="i")
        deltas, _ = await adelete_relations(
            catalog,
            {
                "sections": ChildSpec(
                    model=Section,
                    fk="catalog",
                    children={"items": ChildSpec(model=Item, fk="section")},
                ),
            },
            context=_JUNK,
        )
        assert not await Item.objects.aexists()
        assert deltas[0].deleted == (section.pk,)
