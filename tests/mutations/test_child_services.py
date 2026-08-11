"""Per-child service slots on ``ChildSpec``.

The spec owns reconciliation, the service owns the row: these assert what the
service actually receives, that the loop's own seeds win over the caller
context, and that a nested service never opens a savepoint of its own.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import transaction

from rest_framework_services import (
    ChildSpec,
    acreate_from_input,
    aupdate_from_input,
    create_from_input,
    run_service,
    update_from_input,
)
from rest_framework_services.mutations.utils import adelete_children, delete_children
from tests.testapp.models import Catalog, Item, Note, Section

_USER = "acting-caller"


def _sections(**kw: Any) -> dict[str, ChildSpec]:
    return {"sections": ChildSpec(model=Section, fk="catalog", **kw)}


@pytest.mark.django_db
class TestCreateService:
    def test_receives_context_and_loop_seeds(self) -> None:
        seen: dict[str, Any] = {}

        def create_section(**pool: Any) -> Section:
            seen.update(pool)
            return Section.objects.create(**pool["data"])

        result = create_from_input(
            Catalog,
            {"name": "c", "sections": [{"title": "s"}]},
            children=_sections(create_service=create_section),
            context={"user": _USER, "progress": "reporter"},
        )
        catalog = result.instance
        assert seen["user"] == _USER  # from the caller context
        assert seen["progress"] == "reporter"
        assert seen["parent"] == catalog
        # The spec, not the service, links the row to its parent.
        assert seen["data"] == {"title": "s", "catalog": catalog}
        # A create pool never fakes an ``instance``; that is the update shape.
        assert "instance" not in seen
        assert catalog.sections.get().title == "s"
        assert result.get_child_change("sections").created == (catalog.sections.get().pk,)

    def test_context_cannot_shadow_the_loops_own_seeds(self) -> None:
        seen: dict[str, Any] = {}

        def create_section(**pool: Any) -> Section:
            seen.update(pool)
            return Section.objects.create(**pool["data"])

        result = create_from_input(
            Catalog,
            {"name": "c", "sections": [{"title": "s"}]},
            children=_sections(create_service=create_section),
            context={"data": "hijacked", "parent": "hijacked", "instance": "hijacked"},
        )
        assert seen["data"] == {"title": "s", "catalog": result.instance}
        assert seen["parent"] == result.instance
        # ``instance`` is not a create seed, so the context value survives —
        # the rule is that the loop's own values win, not that context is scrubbed.
        assert seen["instance"] == "hijacked"

    def test_service_receives_only_what_it_declares(self) -> None:
        def create_section(*, data: Any) -> Section:
            return Section.objects.create(**data)

        result = create_from_input(
            Catalog,
            {"name": "c", "sections": [{"title": "s"}]},
            children=_sections(create_service=create_section),
            context={"user": _USER, "request": object()},
        )
        assert result.instance.sections.get().title == "s"

    def test_runs_on_the_update_path_too(self) -> None:
        catalog = Catalog.objects.create(name="c")
        calls: list[Any] = []

        def create_section(*, data: Any, parent: Any) -> Section:
            calls.append(parent)
            return Section.objects.create(**data)

        update_from_input(
            catalog,
            {"sections": [{"title": "new"}]},
            children=_sections(create_service=create_section),
        )
        assert calls == [catalog]
        assert catalog.sections.get().title == "new"


@pytest.mark.django_db
class TestUpdateService:
    def test_receives_instance_data_and_parent(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="old")
        seen: dict[str, Any] = {}

        def update_section(**pool: Any) -> Section:
            seen.update(pool)
            instance = pool["instance"]
            instance.title = pool["data"]["title"]
            instance.save(update_fields=["title"])
            return instance

        result = update_from_input(
            catalog,
            {"sections": [{"pk": section.pk, "title": "new"}]},
            children=_sections(update_service=update_section),
            context={"user": _USER},
        )
        assert seen["user"] == _USER
        assert seen["instance"] == section
        assert seen["parent"] == catalog
        assert seen["data"] == {"pk": section.pk, "title": "new"}
        section.refresh_from_db()
        assert section.title == "new"
        assert result.get_child_change("sections").updated == (section.pk,)

    def test_none_return_means_the_in_memory_instance(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="old")

        def update_section(*, instance: Section, data: Any) -> None:
            instance.title = data["title"]
            instance.save(update_fields=["title"])

        result = update_from_input(
            catalog,
            {"sections": [{"pk": section.pk, "title": "new"}]},
            children=_sections(update_service=update_section),
        )
        assert result.get_child_change("sections").updated == (section.pk,)
        section.refresh_from_db()
        assert section.title == "new"

    def test_returned_row_is_the_one_reported(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="old")
        replacement = Section.objects.create(catalog=catalog, title="replacement")

        def update_section(*, instance: Section) -> Section:
            return replacement

        result = update_from_input(
            catalog,
            {"sections": [{"pk": section.pk}, {"pk": replacement.pk}]},
            children=_sections(update_service=update_section, mode="merge"),
        )
        assert result.get_child_change("sections").updated == (replacement.pk, replacement.pk)

    def test_context_cannot_shadow_instance_or_data(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="old")
        seen: dict[str, Any] = {}

        def update_section(**pool: Any) -> None:
            seen.update(pool)

        update_from_input(
            catalog,
            {"sections": [{"pk": section.pk, "title": "new"}]},
            children=_sections(update_service=update_section),
            context={"instance": "hijacked", "data": "hijacked", "parent": "hijacked"},
        )
        assert seen["instance"] == section
        assert seen["data"] == {"pk": section.pk, "title": "new"}
        assert seen["parent"] == catalog


@pytest.mark.django_db
class TestDeleteService:
    def test_replaces_orphan_removal_including_the_unlink_rule(self) -> None:
        catalog = Catalog.objects.create(name="c")
        orphan = Note.objects.create(catalog=catalog, body="orphan")  # nullable FK
        seen: dict[str, Any] = {}

        def archive(**pool: Any) -> None:
            seen.update(pool)
            note = pool["instance"]
            note.body = "archived"
            note.save(update_fields=["body"])

        result = update_from_input(
            catalog,
            {"notes": []},
            children={"notes": ChildSpec(model=Note, fk="catalog", delete_service=archive)},
            context={"user": _USER},
        )
        assert seen["user"] == _USER
        assert seen["instance"] == orphan
        assert seen["parent"] == catalog
        orphan.refresh_from_db()
        assert orphan.body == "archived"
        assert orphan.catalog_id == catalog.pk  # the service, not the unlink rule, ran
        change = result.get_child_change("notes")
        # The loop can no longer tell an unlink from a delete, so it reports
        # neither: "removed" is the one thing it still knows to be true.
        assert change.removed == (orphan.pk,)
        assert change.deleted == ()
        assert change.unlinked == ()

    def test_replaces_the_delete_cascade(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="s")
        item = Item.objects.create(section=section, label="i")
        archived: list[Any] = []

        def archive(*, instance: Section, parent: Any, user: Any) -> None:
            archived.append((instance.pk, parent, user))

        deltas = delete_children(
            catalog,
            {
                "sections": ChildSpec(
                    model=Section,
                    fk="catalog",
                    delete_service=archive,
                    children={"items": ChildSpec(model=Item, fk="section")},
                ),
            },
            context={"user": _USER},
        )
        # Grandchildren are still the spec's business — removed before the row.
        assert not Item.objects.filter(pk=item.pk).exists()
        assert archived == [(section.pk, catalog, _USER)]
        assert Section.objects.filter(pk=section.pk).exists()  # the service kept it
        assert deltas[0].removed == (section.pk,)
        assert deltas[0].deleted == ()


@pytest.mark.django_db
class TestNoSavepointPerRow:
    def test_nested_service_opens_no_savepoint_of_its_own(self) -> None:
        seen: dict[str, Any] = {}

        def create_section(*, data: Any) -> Section:
            seen["nested"] = list(transaction.get_connection().savepoint_ids)
            return Section.objects.create(**data)

        def probe(**_: Any) -> None:
            seen["atomic"] = list(transaction.get_connection().savepoint_ids)

        with transaction.atomic():
            seen["outer"] = list(transaction.get_connection().savepoint_ids)
            create_from_input(
                Catalog,
                {"name": "c", "sections": [{"title": "s"}]},
                children=_sections(create_service=create_section),
            )
            # The contrast: what an ``atomic=True`` invocation would have cost.
            run_service(probe, {}, atomic=True)

        assert seen["nested"] == seen["outer"]
        assert len(seen["atomic"]) == len(seen["outer"]) + 1


@pytest.mark.django_db(transaction=True)
class TestAsyncChildServices:
    async def test_acreate_service_receives_the_pool(self) -> None:
        seen: dict[str, Any] = {}

        async def create_section(**pool: Any) -> Section:
            seen.update(pool)
            return await Section.objects.acreate(**pool["data"])

        result = await acreate_from_input(
            Catalog,
            {"name": "c", "sections": [{"title": "s"}]},
            children=_sections(create_service=create_section),
            context={"user": _USER, "data": "hijacked", "parent": "hijacked"},
        )
        catalog = result.instance
        assert seen["user"] == _USER
        assert seen["parent"] == catalog
        assert seen["data"] == {"title": "s", "catalog": catalog}
        assert await catalog.sections.acount() == 1

    async def test_nested_service_runs_without_a_transaction_of_its_own(self) -> None:
        # An async service sees its own connection object, so ``in_atomic_block``
        # reads the same either way and cannot decide this. Rollback can: with
        # ``atomic=True`` the row this service wrote before raising would be
        # rolled back, and there is no outer transaction here to hide that.
        async def create_section(*, data: Any) -> Section:
            await Section.objects.acreate(**data)
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await acreate_from_input(
                Catalog,
                {"name": "c", "sections": [{"title": "s"}]},
                children=_sections(create_service=create_section),
            )
        assert await Section.objects.acount() == 1

    async def test_aupdate_service_none_return_and_returned_row(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        section = await Section.objects.acreate(catalog=catalog, title="old")
        seen: dict[str, Any] = {}

        async def update_section(**pool: Any) -> None:
            seen.update(pool)
            instance = pool["instance"]
            instance.title = "new"
            await instance.asave(update_fields=["title"])

        result = await aupdate_from_input(
            catalog,
            {"sections": [{"pk": section.pk}]},
            children=_sections(update_service=update_section),
            context={"user": _USER, "instance": "hijacked"},
        )
        assert seen["instance"] == section
        assert seen["user"] == _USER
        assert result.get_child_change("sections").updated == (section.pk,)
        await section.arefresh_from_db()
        assert section.title == "new"

    async def test_aupdate_service_returning_a_row(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        section = await Section.objects.acreate(catalog=catalog, title="old")

        async def update_section(*, instance: Section) -> Section:
            return instance

        result = await aupdate_from_input(
            catalog,
            {"sections": [{"pk": section.pk}]},
            children=_sections(update_service=update_section, mode="merge"),
        )
        assert result.get_child_change("sections").updated == (section.pk,)

    async def test_adelete_service_on_orphans_and_cascade(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        orphan = await Note.objects.acreate(catalog=catalog, body="orphan")
        archived: list[Any] = []

        async def archive(*, instance: Note, parent: Any) -> None:
            archived.append((instance.pk, parent))

        result = await aupdate_from_input(
            catalog,
            {"notes": []},
            children={"notes": ChildSpec(model=Note, fk="catalog", delete_service=archive)},
        )
        assert archived == [(orphan.pk, catalog)]
        assert result.get_child_change("notes").removed == (orphan.pk,)
        assert result.get_child_change("notes").unlinked == ()
        await orphan.arefresh_from_db()
        assert orphan.catalog_id == catalog.pk

        deltas = await adelete_children(
            catalog,
            {"notes": ChildSpec(model=Note, fk="catalog", delete_service=archive)},
            context={},
        )
        assert deltas[0].removed == (orphan.pk,)
        assert archived == [(orphan.pk, catalog), (orphan.pk, catalog)]
