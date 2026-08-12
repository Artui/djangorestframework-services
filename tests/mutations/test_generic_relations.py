"""Generic relations: the child collection whose link is a content type.

Same reconciliation as a reverse foreign key -- matched inside the parent's own
accessor, orphans unlinked when the link is nullable and deleted when it is not
-- with the one column replaced by two, and the content type resolved on first
use rather than imported with the package.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_services import (
    ChildSpec,
    GenericRelationSpec,
    acreate_from_input,
    aupdate_from_input,
    create_from_input,
    update_from_input,
)
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from tests.testapp.models import Annotation, Attachment, Catalog, Item, Section, Tag

_ATTACHMENTS = {"attachments": GenericRelationSpec(model=Attachment)}
_ANNOTATIONS = {
    "annotations": GenericRelationSpec(
        model=Annotation, content_type_field="kind", object_id_field="row_id"
    )
}


@pytest.mark.django_db
class TestTheParentsContentTypeAndKeyAreInjected:
    def test_create_links_each_row_to_the_parent(self) -> None:
        result = create_from_input(
            Catalog,
            {"name": "c", "attachments": [{"label": "a"}, {"label": "b"}]},
            relations=_ATTACHMENTS,
        )

        catalog = result.instance
        assert sorted(catalog.attachments.values_list("label", flat=True)) == ["a", "b"]
        row = Attachment.objects.get(label="a")
        assert row.owner == catalog
        assert row.object_id == catalog.pk
        delta = result.get_child_change("attachments")
        assert len(delta.created) == 2

    def test_the_link_columns_may_be_named_anything(self) -> None:
        result = create_from_input(
            Catalog,
            {"name": "c", "annotations": [{"text": "note"}]},
            relations=_ANNOTATIONS,
        )

        annotation = Annotation.objects.get()
        assert annotation.owner == result.instance
        assert annotation.row_id == result.instance.pk

    def test_update_matches_inside_the_parents_own_accessor(self) -> None:
        catalog = Catalog.objects.create(name="c")
        other = Catalog.objects.create(name="other")
        mine = catalog.attachments.create(label="mine")
        theirs = other.attachments.create(label="theirs")

        result = update_from_input(
            catalog,
            {"attachments": [{"pk": mine.pk, "label": "renamed"}]},
            relations=_ATTACHMENTS,
        )

        mine.refresh_from_db()
        theirs.refresh_from_db()
        assert mine.label == "renamed"
        assert theirs.label == "theirs"
        assert result.get_child_change("attachments").updated == (mine.pk,)

    def test_a_relation_the_input_omits_is_untouched(self) -> None:
        catalog = Catalog.objects.create(name="c")
        catalog.attachments.create(label="keep")

        result = update_from_input(catalog, {"name": "renamed"}, relations=_ATTACHMENTS)

        assert catalog.attachments.count() == 1
        assert not result.get_child_change("attachments")

    def test_a_row_carries_its_own_shaping_and_nesting(self) -> None:
        tag = Tag.objects.create(name="t")
        create_from_input(
            Catalog,
            {
                "name": "c",
                "attachments": [{"caption": "a", "junk": 1}],
                "sections": [{"title": "s"}],
            },
            relations={
                "attachments": GenericRelationSpec(
                    model=Attachment,
                    field_map={"caption": "label"},
                    exclude_fields=["junk"],
                ),
                "sections": ChildSpec(
                    model=Section,
                    fk="catalog",
                    m2m=lambda row: {"tags": [tag]},
                    children={"items": ChildSpec(model=Item, fk="section")},
                ),
            },
        )

        assert Attachment.objects.get().label == "a"


@pytest.mark.django_db
class TestOrphansFollowTheSameUnlinkOrDeleteRule:
    def test_a_non_nullable_link_deletes_the_orphan(self) -> None:
        catalog = Catalog.objects.create(name="c")
        kept = catalog.attachments.create(label="kept")
        dropped = catalog.attachments.create(label="dropped")

        result = update_from_input(
            catalog,
            {"attachments": [{"pk": kept.pk}]},
            relations=_ATTACHMENTS,
        )

        assert result.get_child_change("attachments").deleted == (dropped.pk,)
        assert not Attachment.objects.filter(pk=dropped.pk).exists()

    def test_a_nullable_link_blanks_both_columns_instead(self) -> None:
        catalog = Catalog.objects.create(name="c")
        dropped = catalog.annotations.create(text="dropped")

        result = update_from_input(catalog, {"annotations": []}, relations=_ANNOTATIONS)

        assert result.get_child_change("annotations").unlinked == (dropped.pk,)
        dropped.refresh_from_db()
        # A link is severed or it is not -- half of one is a row pointing at a
        # content type with no row id.
        assert (dropped.kind_id, dropped.row_id) == (None, None)

    def test_merge_removes_nothing(self) -> None:
        catalog = Catalog.objects.create(name="c")
        existing = catalog.attachments.create(label="existing")

        update_from_input(
            catalog,
            {"attachments": [{"label": "added"}]},
            relations={"attachments": GenericRelationSpec(model=Attachment, mode="merge")},
        )

        assert catalog.attachments.count() == 2
        assert Attachment.objects.filter(pk=existing.pk).exists()

    def test_an_unknown_mode_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="GenericRelationSpec.mode must be one of"):
            GenericRelationSpec(model=Attachment, mode="upsert")


@pytest.mark.django_db
class TestAGenericRowCannotBeCreatedUnderAForeignPrimaryKey:
    """A generic row is created like any other, so ``Attachment(pk=7,
    content_type=..., object_id=...).save()`` reassigns attachment 7 to this
    parent and overwrites it.

    The match is scoped to the parent's own accessor, so a pk belonging to a
    different parent walks straight past it into the create.
    """

    def test_another_parents_row_is_refused(self) -> None:
        catalog = Catalog.objects.create(name="c")
        other = Catalog.objects.create(name="other")
        theirs = other.attachments.create(label="secret")

        with pytest.raises(ServiceValidationError) as excinfo:
            update_from_input(
                catalog,
                {"attachments": [{"pk": theirs.pk, "label": "pwned"}]},
                relations=_ATTACHMENTS,
            )

        assert "attachments" in excinfo.value.detail
        theirs.refresh_from_db()
        assert theirs.label == "secret"
        assert theirs.owner == other

    def test_the_row_service_is_never_handed_the_key(self) -> None:
        catalog = Catalog.objects.create(name="c")
        other = Catalog.objects.create(name="other")
        theirs = other.attachments.create(label="secret")
        seen: list[dict[str, Any]] = []

        def create_attachment(*, data: dict[str, Any]) -> Attachment:
            seen.append(dict(data))
            return Attachment.objects.create(**data)

        with pytest.raises(ServiceValidationError):
            update_from_input(
                catalog,
                {"attachments": [{"pk": theirs.pk, "label": "pwned"}]},
                relations={
                    "attachments": GenericRelationSpec(
                        model=Attachment, create_service=create_attachment
                    )
                },
            )

        assert seen == [], "the service was reached with a foreign primary key"
        theirs.refresh_from_db()
        assert theirs.label == "secret"


@pytest.mark.django_db
class TestARowServiceOwnsTheRow:
    def test_the_create_slot_receives_the_link_already_resolved(self) -> None:
        seen: list[dict[str, Any]] = []

        def create_attachment(*, data: dict[str, Any], parent: Catalog) -> Attachment:
            seen.append(dict(data))
            return Attachment.objects.create(**data)

        create_from_input(
            Catalog,
            {"name": "c", "attachments": [{"label": "a"}]},
            relations={
                "attachments": GenericRelationSpec(
                    model=Attachment, create_service=create_attachment
                )
            },
        )

        assert set(seen[0]) == {"label", "content_type", "object_id"}

    def test_the_delete_slot_replaces_the_removal_rule(self) -> None:
        catalog = Catalog.objects.create(name="c")
        dropped = catalog.attachments.create(label="dropped")
        archived: list[int] = []

        def archive(*, instance: Attachment) -> None:
            archived.append(instance.pk)

        result = update_from_input(
            catalog,
            {"attachments": []},
            relations={
                "attachments": GenericRelationSpec(model=Attachment, delete_service=archive)
            },
        )

        assert archived == [dropped.pk]
        delta = result.get_child_change("attachments")
        assert (delta.removed, delta.deleted, delta.unlinked) == ((dropped.pk,), (), ())
        assert Attachment.objects.filter(pk=dropped.pk).exists()


@pytest.mark.django_db(transaction=True)
class TestTheAsyncPathWritesTheSameWay:
    async def test_create_links_the_rows(self) -> None:
        result = await acreate_from_input(
            Catalog,
            {"name": "c", "attachments": [{"label": "a"}]},
            relations=_ATTACHMENTS,
        )

        assert await result.instance.attachments.acount() == 1
        row = await Attachment.objects.aget()
        assert row.object_id == result.instance.pk

    async def test_update_reconciles_and_deletes_the_orphan(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        kept = await Attachment.objects.acreate(
            label="kept",
            content_type=await _content_type(Catalog),
            object_id=catalog.pk,
        )
        dropped = await Attachment.objects.acreate(
            label="dropped",
            content_type=await _content_type(Catalog),
            object_id=catalog.pk,
        )

        result = await aupdate_from_input(
            catalog,
            {"attachments": [{"pk": kept.pk, "label": "renamed"}]},
            relations=_ATTACHMENTS,
        )

        delta = result.get_child_change("attachments")
        assert (delta.updated, delta.deleted) == ((kept.pk,), (dropped.pk,))
        assert not await Attachment.objects.filter(pk=dropped.pk).aexists()

    async def test_a_nullable_link_unlinks_on_the_async_path_too(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        dropped = await Annotation.objects.acreate(
            text="dropped", kind=await _content_type(Catalog), row_id=catalog.pk
        )

        result = await aupdate_from_input(catalog, {"annotations": []}, relations=_ANNOTATIONS)

        assert result.get_child_change("annotations").unlinked == (dropped.pk,)
        refreshed = await Annotation.objects.aget(pk=dropped.pk)
        assert (refreshed.kind_id, refreshed.row_id) == (None, None)

    async def test_an_omitted_relation_is_untouched(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")

        result = await aupdate_from_input(catalog, {"name": "renamed"}, relations=_ATTACHMENTS)

        assert not result.get_child_change("attachments")

    async def test_a_foreign_primary_key_is_refused_here_too(self) -> None:
        catalog = await Catalog.objects.acreate(name="c")
        other = await Catalog.objects.acreate(name="other")
        theirs = await Attachment.objects.acreate(
            label="secret", content_type=await _content_type(Catalog), object_id=other.pk
        )

        with pytest.raises(ServiceValidationError):
            await aupdate_from_input(
                catalog,
                {"attachments": [{"pk": theirs.pk, "label": "pwned"}]},
                relations=_ATTACHMENTS,
            )

        refreshed = await Attachment.objects.aget(pk=theirs.pk)
        assert (refreshed.label, refreshed.object_id) == ("secret", other.pk)


async def _content_type(model: type[Any]) -> Any:
    """The parent's content type, fetched off the event loop."""
    from asgiref.sync import sync_to_async
    from django.contrib.contenttypes.models import ContentType

    return await sync_to_async(ContentType.objects.get_for_model, thread_sensitive=True)(model)


class TestTheContentTypeAppIsAGate:
    def test_the_lookup_names_the_missing_app_and_the_remedy(self, monkeypatch: Any) -> None:
        from django.apps import apps

        from rest_framework_services.mutations.utils import _content_type_for

        monkeypatch.setattr(apps, "is_installed", lambda label: False)
        with pytest.raises(ImproperlyConfigured) as excinfo:
            _content_type_for(Catalog())

        message = str(excinfo.value)
        assert "django.contrib.contenttypes" in message
        assert "INSTALLED_APPS" in message
        assert "ChildSpec" in message
