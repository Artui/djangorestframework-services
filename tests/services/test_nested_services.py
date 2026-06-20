"""``children=`` passthrough on the default model service factories (NEST-1b)."""

from __future__ import annotations

import pytest

from rest_framework_services import (
    ChildSpec,
    acreate_model,
    adelete_model,
    aupdate_model,
    create_model,
    delete_model,
    update_model,
)
from tests.testapp.models import Catalog, Item, Note, Section

_SECTIONS = ChildSpec(model=Section, fk="catalog")


@pytest.mark.django_db
class TestSyncServices:
    def test_create_model_writes_children(self) -> None:
        service = create_model(Catalog, children={"sections": _SECTIONS})
        catalog = service(data={"name": "c", "sections": [{"title": "s"}]})
        assert catalog.sections.get().title == "s"

    def test_update_model_reconciles_children(self) -> None:
        catalog = Catalog.objects.create(name="c")
        orphan = Section.objects.create(catalog=catalog, title="orphan")
        service = update_model(Catalog, children={"sections": _SECTIONS})
        service(instance=catalog, data={"sections": [{"title": "new"}]})
        assert not Section.objects.filter(pk=orphan.pk).exists()  # replace removed orphan
        assert catalog.sections.get().title == "new"

    def test_delete_model_cascades_children_then_parent(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="s")
        Item.objects.create(section=section, label="i")
        note = Note.objects.create(catalog=catalog, body="n")
        service = delete_model(
            Catalog,
            children={
                "sections": ChildSpec(
                    model=Section,
                    fk="catalog",
                    children={"items": ChildSpec(model=Item, fk="section")},
                ),
                "notes": ChildSpec(model=Note, fk="catalog"),
            },
        )
        service(instance=catalog)
        assert not Catalog.objects.filter(pk=catalog.pk).exists()
        assert not Section.objects.exists()
        assert not Item.objects.exists()
        note.refresh_from_db()
        assert note.catalog_id is None


@pytest.mark.django_db(transaction=True)
class TestAsyncServices:
    async def test_acreate_model_writes_children(self) -> None:
        service = acreate_model(Catalog, children={"sections": _SECTIONS})
        catalog = await service(data={"name": "c", "sections": [{"title": "s"}]})
        assert await catalog.sections.acount() == 1

    async def test_aupdate_model_reconciles_children(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        orphan = await Section.objects.acreate(catalog=catalog, title="orphan")
        service = aupdate_model(Catalog, children={"sections": _SECTIONS})
        await service(instance=catalog, data={"sections": [{"title": "new"}]})
        assert not await Section.objects.filter(pk=orphan.pk).aexists()

    async def test_adelete_model_cascades_children(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        section = await Section.objects.acreate(catalog=catalog, title="s")
        await Item.objects.acreate(section=section, label="i")
        service = adelete_model(
            Catalog,
            children={
                "sections": ChildSpec(
                    model=Section,
                    fk="catalog",
                    children={"items": ChildSpec(model=Item, fk="section")},
                ),
            },
        )
        await service(instance=catalog)
        assert not await Catalog.objects.aexists()
        assert not await Item.objects.aexists()
