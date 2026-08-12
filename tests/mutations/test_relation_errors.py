"""What a row's write raises, and where the caller finds it.

A service in a relation slot raises about *its* row. These assert the error
arrives under the relation that carried it — at the right position when the
relation holds many rows — in the shape DRF's ``ListSerializer`` uses, and that
what the service actually said survives the trip.

The services here refuse the row marked ``"rude"`` and write the others, so a
reported position is measured against rows that really did pass.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.exceptions import ValidationError

from rest_framework_services import (
    ChildSpec,
    ForwardRelationSpec,
    GenericRelationSpec,
    ManyToManySpec,
    ReverseOneToOneSpec,
    ServiceValidationError,
    acreate_from_input,
    aupdate_from_input,
    create_from_input,
    update_from_input,
)
from tests.testapp.models import (
    Attachment,
    Author,
    Catalog,
    Item,
    Post,
    Profile,
    Section,
    Tag,
)

_RUDE: dict[str, Any] = {"title": ["Too rude."]}


def _writes_all_but_the_rude_row(model: Any, detail: Any = None) -> Any:
    """A create service that refuses one row of an incoming set and writes the rest."""

    def service(*, data: dict[str, Any]) -> Any:
        if "rude" in data.values():
            raise ServiceValidationError(_RUDE if detail is None else detail)
        return model.objects.create(**data)

    return service


def _awrites_all_but_the_rude_row(model: Any, detail: Any = None) -> Any:
    """The async twin of :func:`_writes_all_but_the_rude_row`."""

    async def service(*, data: dict[str, Any]) -> Any:
        if "rude" in data.values():
            raise ServiceValidationError(_RUDE if detail is None else detail)
        return await model.objects.acreate(**data)

    return service


def _refuses(detail: Any = None) -> Any:
    """A row service that refuses whatever it is handed."""

    def service(**_: Any) -> None:
        raise ServiceValidationError(_RUDE if detail is None else detail)

    return service


def _arefuses(detail: Any = None) -> Any:
    """The async twin of :func:`_refuses`."""

    async def service(**_: Any) -> None:
        raise ServiceValidationError(_RUDE if detail is None else detail)

    return service


@pytest.mark.django_db
class TestACollectionSaysWhichRow:
    def test_a_create_lands_at_its_index(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Catalog,
                {"name": "c", "sections": [{"title": "ok"}, {"title": "rude"}, {"title": "fine"}]},
                children={
                    "sections": ChildSpec(
                        model=Section,
                        fk="catalog",
                        create_service=_writes_all_but_the_rude_row(Section),
                    )
                },
            )
        # A list as long as the one that was sent, so the caller can pair it
        # with the payload row by row -- DRF's ``ListSerializer`` shape.
        assert excinfo.value.detail == {"sections": [{}, _RUDE, {}]}

    def test_an_update_lands_at_its_index(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="old")

        with pytest.raises(ServiceValidationError) as excinfo:
            update_from_input(
                catalog,
                {"sections": [{"title": "new"}, {"pk": section.pk, "title": "rude"}]},
                children={
                    "sections": ChildSpec(model=Section, fk="catalog", update_service=_refuses())
                },
            )
        assert excinfo.value.detail == {"sections": [{}, _RUDE]}

    def test_a_generic_relation_reports_the_same_way(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Catalog,
                {"name": "c", "attachments": [{"label": "ok"}, {"label": "rude"}]},
                relations={
                    "attachments": GenericRelationSpec(
                        model=Attachment,
                        create_service=_writes_all_but_the_rude_row(
                            Attachment, {"label": ["Nope."]}
                        ),
                    )
                },
            )
        assert excinfo.value.detail == {"attachments": [{}, {"label": ["Nope."]}]}

    def test_a_many_to_many_target_reports_the_same_way(self) -> None:
        catalog = Catalog.objects.create(name="c")
        section = Section.objects.create(catalog=catalog, title="s")

        with pytest.raises(ServiceValidationError) as excinfo:
            update_from_input(
                section,
                {"tags": [{"name": "ok"}, {"name": "rude"}]},
                relations={
                    "tags": ManyToManySpec(
                        model=Tag, create_service=_writes_all_but_the_rude_row(Tag)
                    )
                },
            )
        assert excinfo.value.detail == {"tags": [{}, _RUDE]}


@pytest.mark.django_db
class TestASingularRelationSaysItsName:
    def test_a_reverse_one_to_one_reports_under_the_relation(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Author,
                {"name": "a", "profile": {"bio": "..."}},
                relations={
                    "profile": ReverseOneToOneSpec(
                        model=Profile,
                        fk="author",
                        create_service=_refuses({"bio": ["Too long."]}),
                    )
                },
            )
        # No list: the relation holds one row, so there is no position to give.
        assert excinfo.value.detail == {"profile": {"bio": ["Too long."]}}

    def test_a_forward_relation_reports_under_the_relation(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Post,
                {"title": "t", "author": {"name": "a"}},
                relations={
                    "author": ForwardRelationSpec(
                        model=Author, create_service=_refuses({"name": ["Too short."]})
                    )
                },
            )
        assert excinfo.value.detail == {"author": {"name": ["Too short."]}}

    def test_an_update_service_reports_under_the_relation(self) -> None:
        author = Author.objects.create(name="a")
        Profile.objects.create(author=author, bio="before")

        with pytest.raises(ServiceValidationError) as excinfo:
            update_from_input(
                author,
                {"profile": {"bio": "after"}},
                relations={
                    "profile": ReverseOneToOneSpec(
                        model=Profile,
                        fk="author",
                        update_service=_refuses({"bio": ["Too long."]}),
                    )
                },
            )
        assert excinfo.value.detail == {"profile": {"bio": ["Too long."]}}

    def test_the_parents_own_field_is_told_apart_from_the_rows(self) -> None:
        # The collision this exists to end: a service refusing a section's
        # ``title`` used to arrive looking exactly like the catalog's own.
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Catalog,
                {"name": "c", "sections": [{"title": "rude"}]},
                children={
                    "sections": ChildSpec(model=Section, fk="catalog", create_service=_refuses())
                },
            )
        assert excinfo.value.detail == {"sections": [_RUDE]}


@pytest.mark.django_db
class TestTheNamesNest:
    def test_a_grandchilds_error_carries_both_relations(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Catalog,
                {
                    "name": "c",
                    "sections": [{"title": "s", "items": [{"label": "ok"}, {"label": "rude"}]}],
                },
                children={
                    "sections": ChildSpec(
                        model=Section,
                        fk="catalog",
                        children={
                            "items": ChildSpec(
                                model=Item,
                                fk="section",
                                create_service=_writes_all_but_the_rude_row(
                                    Item, {"label": ["No."]}
                                ),
                            )
                        },
                    )
                },
            )
        # In the order a reader walks them: the parent's relation outermost.
        assert excinfo.value.detail == {"sections": [{"items": [{}, {"label": ["No."]}]}]}

    def test_the_primary_key_guard_is_not_named_twice(self) -> None:
        # It names the relation itself, so the row writer leaves it alone --
        # this is the shape it has reported since it shipped.
        catalog = Catalog.objects.create(name="c")

        with pytest.raises(ServiceValidationError) as excinfo:
            update_from_input(
                catalog,
                {"sections": [{"pk": 4242, "title": "t"}]},
                children={"sections": ChildSpec(model=Section, fk="catalog")},
            )
        detail = excinfo.value.detail
        assert isinstance(detail, dict)
        assert list(detail) == ["sections"]
        assert "references Section [4242]" in detail["sections"][0]


@pytest.mark.django_db
class TestWhatTheServiceSaidSurvives:
    def test_a_string_detail_is_not_reshaped(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Catalog,
                {"name": "c", "sections": [{"title": "t"}]},
                children={
                    "sections": ChildSpec(
                        model=Section, fk="catalog", create_service=_refuses("Too rude.")
                    )
                },
            )
        # Under the relation and at the position, but still the string it was.
        assert excinfo.value.detail == {"sections": ["Too rude."]}

    def test_a_list_detail_is_not_reshaped(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            create_from_input(
                Author,
                {"name": "a", "profile": {"bio": "..."}},
                relations={
                    "profile": ReverseOneToOneSpec(
                        model=Profile,
                        fk="author",
                        create_service=_refuses(["Too long.", "Too loud."]),
                    )
                },
            )
        assert excinfo.value.detail == {"profile": ["Too long.", "Too loud."]}

    def test_a_drf_validation_error_stays_a_drf_validation_error(self) -> None:
        # A service that reached for DRF's error chose its status mapping with
        # it, and naming the relation is no reason to overrule that.
        def service(**_: Any) -> None:
            raise ValidationError({"title": ["Too rude."]})

        with pytest.raises(ValidationError) as excinfo:
            create_from_input(
                Catalog,
                {"name": "c", "sections": [{"title": "t"}]},
                children={
                    "sections": ChildSpec(model=Section, fk="catalog", create_service=service)
                },
            )
        assert not isinstance(excinfo.value, ServiceValidationError)
        assert excinfo.value.detail == {"sections": [{"title": ["Too rude."]}]}

    def test_an_error_that_is_not_about_validation_is_left_alone(self) -> None:
        def service(**_: Any) -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            create_from_input(
                Catalog,
                {"name": "c", "sections": [{"title": "t"}]},
                children={
                    "sections": ChildSpec(model=Section, fk="catalog", create_service=service)
                },
            )


@pytest.mark.django_db(transaction=True)
class TestTheAsyncPathReportsIdentically:
    async def test_an_async_create_lands_at_its_index(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            await acreate_from_input(
                Catalog,
                {"name": "c", "sections": [{"title": "ok"}, {"title": "rude"}]},
                children={
                    "sections": ChildSpec(
                        model=Section,
                        fk="catalog",
                        create_service=_awrites_all_but_the_rude_row(Section),
                    )
                },
            )
        assert excinfo.value.detail == {"sections": [{}, _RUDE]}

    async def test_an_async_update_reports_under_the_relation(self) -> None:
        author = await Author.objects.acreate(name="a")
        await Profile.objects.acreate(author=author, bio="before")

        with pytest.raises(ServiceValidationError) as excinfo:
            await aupdate_from_input(
                author,
                {"profile": {"bio": "after"}},
                relations={
                    "profile": ReverseOneToOneSpec(
                        model=Profile,
                        fk="author",
                        update_service=_arefuses({"bio": ["Too long."]}),
                    )
                },
            )
        assert excinfo.value.detail == {"profile": {"bio": ["Too long."]}}

    async def test_an_async_grandchild_carries_both_relations(self) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            await acreate_from_input(
                Catalog,
                {
                    "name": "c",
                    "sections": [{"title": "s", "items": [{"label": "ok"}, {"label": "rude"}]}],
                },
                children={
                    "sections": ChildSpec(
                        model=Section,
                        fk="catalog",
                        children={
                            "items": ChildSpec(
                                model=Item,
                                fk="section",
                                create_service=_awrites_all_but_the_rude_row(
                                    Item, {"label": ["No."]}
                                ),
                            )
                        },
                    )
                },
            )
        assert excinfo.value.detail == {"sections": [{"items": [{}, {"label": ["No."]}]}]}
